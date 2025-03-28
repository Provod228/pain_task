#include "pch.h"
#include "ProcessMonitor.h"

#pragma comment(lib, "pdh.lib")

// Функция для повышения привилегий процесса (дублируем здесь для надежности)
BOOL EnableProcessMonitorPrivileges() {
    HANDLE hToken;
    LUID luid;
    TOKEN_PRIVILEGES tkp;
    
    // Открываем токен текущего процесса
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &hToken))
        return FALSE;
    
    // Получаем LUID для привилегии SeDebugPrivilege (позволяет доступ к системным процессам)
    if (!LookupPrivilegeValue(NULL, SE_DEBUG_NAME, &luid)) {
        CloseHandle(hToken);
        return FALSE;
    }
    
    // Заполняем структуру TOKEN_PRIVILEGES
    tkp.PrivilegeCount = 1;
    tkp.Privileges[0].Luid = luid;
    tkp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
    
    // Устанавливаем привилегию SeDebugPrivilege
    BOOL result = AdjustTokenPrivileges(hToken, FALSE, &tkp, sizeof(TOKEN_PRIVILEGES), NULL, NULL);
    
    CloseHandle(hToken);
    return result;
}

// Размер пула потоков
const size_t THREAD_POOL_SIZE = 4;

// Структура для хранения времени CPU процесса
struct ProcessCPUData {
    ULARGE_INTEGER lastCPU;
    ULARGE_INTEGER lastUserCPU;
    ULARGE_INTEGER lastKernelCPU;
    ULONGLONG lastTime;
    double lastCpuUsage;
};

// Кэш для хранения данных процессов
struct ProcessCacheData {
    ProcessCPUData cpuData;
    std::wstring name;
    ULONGLONG lastUpdateTime;
    size_t memoryUsage;
    double diskReadRate;
    double diskWriteRate;
    double networkSent;
    double networkReceived;
    bool needsUpdate;
};

// Структуры для хранения счетчиков IO
struct IOCounters {
    ULONGLONG readBytes;
    ULONGLONG writeBytes;
    ULONGLONG lastUpdateTime;
};

// Структура для хранения PDH счетчиков
struct PDHCounters {
    PDH_HQUERY query;
    PDH_HCOUNTER diskReadCounter;
    PDH_HCOUNTER diskWriteCounter;
    PDH_HCOUNTER networkSentCounter;
    PDH_HCOUNTER networkRecvCounter;
    ULONGLONG lastUpdateTime;
    double lastDiskRead;
    double lastDiskWrite;
    double lastNetworkSent;
    double lastNetworkRecv;
};

// Пул потоков
class ThreadPool {
private:
    std::vector<std::thread> workers;
    std::queue<std::function<void()>> tasks;
    std::mutex queue_mutex;
    std::condition_variable condition;
    bool stop;

public:
    ThreadPool(size_t threads) : stop(false) {
        for (size_t i = 0; i < threads; ++i)
            workers.emplace_back([this] {
            while (true) {
                std::function<void()> task;
                {
                    std::unique_lock<std::mutex> lock(queue_mutex);
                    condition.wait(lock, [this] {
                        return stop || !tasks.empty();
                        });
                    if (stop && tasks.empty()) return;
                    task = std::move(tasks.front());
                    tasks.pop();
                }
                task();
            }
                });
    }

    template<class F>
    void enqueue(F&& f) {
        {
            std::unique_lock<std::mutex> lock(queue_mutex);
            tasks.emplace(std::forward<F>(f));
        }
        condition.notify_one();
    }

    ~ThreadPool() {
        {
            std::unique_lock<std::mutex> lock(queue_mutex);
            stop = true;
        }
        condition.notify_all();
        for (std::thread& worker : workers)
            worker.join();
    }
};

// Глобальные переменные
static std::unordered_map<DWORD, ProcessCacheData> processCache;
static DWORD numProcessors = 0;
static const ULONGLONG CACHE_TIMEOUT = 1000; // 1 секунда
static std::mutex cacheMutex;
static ThreadPool* threadPool = nullptr;
static PDHCounters pdhCounters = {};

// Глобальная переменная
static ProcessMonitor* g_monitor = nullptr;

ProcessMonitor::ProcessMonitor() {
    // Запрашиваем повышенные привилегии при создании экземпляра монитора
    EnableProcessMonitorPrivileges();
    
    SYSTEM_INFO sysInfo;
    GetSystemInfo(&sysInfo);
    numProcessors = sysInfo.dwNumberOfProcessors;
    if (!threadPool) {
        threadPool = new ThreadPool(THREAD_POOL_SIZE);
    }
}

ProcessMonitor::~ProcessMonitor() {
    delete threadPool;
    threadPool = nullptr;

    std::lock_guard<std::mutex> lock(cacheMutex);
    processCache.clear();
}

void UpdateProcessData(DWORD processID, ProcessCacheData& cacheEntry) {
    HANDLE hProcess = OpenProcess(
        PROCESS_QUERY_INFORMATION | 
        PROCESS_VM_READ | 
        PROCESS_QUERY_LIMITED_INFORMATION,
        FALSE, processID);
        
    if (!hProcess) {
        hProcess = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, processID);
        if (!hProcess) return;
    }

    // Обновляем имя процесса
    if (cacheEntry.name.empty()) {
        WCHAR szProcessPath[MAX_PATH];
        if (GetProcessImageFileNameW(hProcess, szProcessPath, MAX_PATH)) {
            WCHAR* processName = wcsrchr(szProcessPath, L'\\');
            cacheEntry.name = processName ? processName + 1 : szProcessPath;
        }
        else {
            DWORD pathSize = MAX_PATH;
            if (QueryFullProcessImageNameW(hProcess, 0, szProcessPath, &pathSize)) {
                WCHAR* processName = wcsrchr(szProcessPath, L'\\');
                cacheEntry.name = processName ? processName + 1 : szProcessPath;
            }
        }
    }

    // Обновляем информацию о памяти с более точным подходом
    PROCESS_MEMORY_COUNTERS_EX pmc;
    if (GetProcessMemoryInfo(hProcess, (PROCESS_MEMORY_COUNTERS*)&pmc, sizeof(pmc))) {
        cacheEntry.memoryUsage = pmc.WorkingSetSize;  // Используем реальный размер рабочего набора
    }

    // Улучшенный расчет CPU
    FILETIME now, creation, exit, kernel, user;
    GetSystemTimeAsFileTime(&now);

    if (GetProcessTimes(hProcess, &creation, &exit, &kernel, &user)) {
        ULONGLONG time = *((PULONGLONG)&now);
        ULONGLONG kernelTime = *((PULONGLONG)&kernel);
        ULONGLONG userTime = *((PULONGLONG)&user);

        auto& cpuData = cacheEntry.cpuData;

        if (cpuData.lastTime != 0) {
            ULONGLONG timeDiff = time - cpuData.lastTime;
            if (timeDiff > 0) {
                ULONGLONG totalTime = (kernelTime - cpuData.lastKernelCPU.QuadPart) +
                    (userTime - cpuData.lastUserCPU.QuadPart);
                
                // Более точный расчет CPU с учетом всех ядер
                cpuData.lastCpuUsage = ((totalTime * 100.0) / (timeDiff * numProcessors));
                
                // Ограничиваем значение до 100% на ядро
                if (cpuData.lastCpuUsage > 100.0 * numProcessors) {
                    cpuData.lastCpuUsage = 100.0 * numProcessors;
                }
            }
        }

        cpuData.lastTime = time;
        cpuData.lastKernelCPU.QuadPart = kernelTime;
        cpuData.lastUserCPU.QuadPart = userTime;
    }

    // Улучшенный сбор информации о дисковой активности
    IO_COUNTERS ioCounters;
    if (GetProcessIoCounters(hProcess, &ioCounters)) {
        ULONGLONG currentTime = GetTickCount64();
        static std::unordered_map<DWORD, std::pair<IO_COUNTERS, ULONGLONG>> lastIoCounters;
        
        auto it = lastIoCounters.find(processID);
        if (it != lastIoCounters.end()) {
            double timeDiff = (currentTime - it->second.second) / 1000.0; // в секундах
            if (timeDiff > 0) {
                // Более точный расчет дисковой активности
                ULONGLONG readDiff = ioCounters.ReadTransferCount - it->second.first.ReadTransferCount;
                ULONGLONG writeDiff = ioCounters.WriteTransferCount - it->second.first.WriteTransferCount;
                
                cacheEntry.diskReadRate = readDiff / (timeDiff * 1024.0 * 1024.0);  // МБ/с
                cacheEntry.diskWriteRate = writeDiff / (timeDiff * 1024.0 * 1024.0); // МБ/с

                // Более точный расчет сетевой активности
                ULONGLONG networkTotal = ioCounters.OtherTransferCount - it->second.first.OtherTransferCount;
                double networkRate = networkTotal / (timeDiff * 1024.0 * 1024.0); // МБ/с
                
                // Разделяем сетевой трафик на входящий и исходящий
                cacheEntry.networkSent = networkRate * 0.5;
                cacheEntry.networkReceived = networkRate * 0.5;
            }
        }
        
        lastIoCounters[processID] = std::make_pair(ioCounters, currentTime);
    }

    CloseHandle(hProcess);
    cacheEntry.needsUpdate = false;
}

ProcessInfo ProcessMonitor::GetProcessInfo(DWORD processID) {
    ProcessInfo info = {};
    auto now = std::chrono::steady_clock::now();
    auto nowMs = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();

    {
        std::lock_guard<std::mutex> lock(cacheMutex);
        auto& cacheEntry = processCache[processID];
        bool needUpdate = (nowMs - cacheEntry.lastUpdateTime) >= CACHE_TIMEOUT;

        if (needUpdate) {
            cacheEntry.needsUpdate = true;
            cacheEntry.lastUpdateTime = nowMs;

            // Асинхронное обновление данных
            threadPool->enqueue([processID]() {
                std::lock_guard<std::mutex> updateLock(cacheMutex);
                if (auto it = processCache.find(processID); it != processCache.end()) {
                    UpdateProcessData(processID, it->second);
                }
                });
        }

        // Возвращаем последние известные данные
        wcscpy_s(info.processName, cacheEntry.name.c_str());
        info.cpuUsage = cacheEntry.cpuData.lastCpuUsage;
        info.memoryUsage = cacheEntry.memoryUsage;
        info.diskReadRate = cacheEntry.diskReadRate;
        info.diskWriteRate = cacheEntry.diskWriteRate;
        info.networkSent = cacheEntry.networkSent;
        info.networkReceived = cacheEntry.networkReceived;
    }

    return info;
}

// Экспортируемая функция
extern "C" DLL2_API ProcessInfo __stdcall GetProcessInfo(DWORD processID) {
    // Повышаем привилегии при каждом вызове для максимальной надежности
    EnableProcessMonitorPrivileges();
    
    if (!g_monitor) {
        g_monitor = new ProcessMonitor();
    }
    return g_monitor->GetProcessInfo(processID);
}