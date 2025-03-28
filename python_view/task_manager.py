import sys
import os
import time
import ctypes
import threading
from datetime import datetime
from collections import deque
import getpass
import warnings

import psutil
import win32security
import win32api
import win32con
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QPointF, QTimer
from PyQt5.QtGui import QColor, QFont, QPalette, QIcon, QPainter
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton, QLabel,
    QGridLayout, QComboBox, QMessageBox, QMenu
)
from PyQt5.QtChart import QChart, QChartView, QLineSeries, QValueAxis
from concurrent.futures import ThreadPoolExecutor

# Импортируем константы для Windows API
from ctypes import wintypes

# Константы для доступа к процессам
SE_DEBUG_NAME = "SeDebugPrivilege"
SE_PRIVILEGE_ENABLED = 0x00000002
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008

# Константы для прав доступа
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001

# Структуры для Windows API
class SYSTEM_PERFORMANCE_INFO(ctypes.Structure):
    _fields_ = [
        ("IdleTime", ctypes.c_int64),
        ("KernelTime", ctypes.c_int64),
        ("UserTime", ctypes.c_int64),
        ("Reserved1", ctypes.c_int64 * 2),
        ("IoReadTransferCount", ctypes.c_int64),
        ("IoWriteTransferCount", ctypes.c_int64),
        ("Reserved2", ctypes.c_int64 * 2),
        ("SystemCalls", ctypes.c_uint32),
    ]

class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t)
    ]

class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]

class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD)
    ]

class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong)
    ]

# Структура для привилегий
class LUID(ctypes.Structure):
    _fields_ = [
        ("LowPart", ctypes.c_ulong),
        ("HighPart", ctypes.c_long)
    ]

class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Luid", LUID),
        ("Attributes", ctypes.c_ulong)
    ]

class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", ctypes.c_ulong),
        ("Privileges", LUID_AND_ATTRIBUTES * 1)
    ]

# Определяем функцию debug_print на уровне модуля (в начале файла)
ENABLE_LOGGING = True  # Включаем логирование для диагностики

def debug_print(*args, **kwargs):
    if ENABLE_LOGGING:
        print(*args, **kwargs)


# Игнорируем предупреждения от PyQt
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

class SystemMetrics:
    def __init__(self):
        # Повышаем привилегии при создании экземпляра
        self._enable_debug_privilege()
        
        # Инициализируем переменные для работы с DLL
        self.is_dll_loaded = False
        self.process_dll = None
        
        # Инициализируем переменные для производительности
        self._prev_cpu_totals = None
        
        # Для дисковой активности
        self._prev_disk_counters = {}
        self._last_disk_update = time.time()
        self._last_disk_counters = None
        self._last_disk_time = time.time()
        
        # Для сетевой активности
        self._prev_net_counters = {}
        self._last_network_update = time.time()
        self._last_network_counters = None
        self._last_network_time = time.time()
        
        self._process_times = {}  # Для расчета CPU
        self._process_io = {}  # Для хранения предыдущих значений IO
        
        # Добавляем словари для хранения истории значений дисковой и сетевой активности
        self._disk_history = {}
        self._network_history = {}
        
        # Инициализируем счетчики производительности
        self._setup_performance_counters()
        
        # Загружаем DLL для мониторинга процессов
        try:
            # Определение путей для скомпилированной версии и обычного запуска
            dll_paths = []
            
            if getattr(sys, 'frozen', False):
                # Если запущено как exe (PyInstaller)
                base_path = os.path.dirname(sys.executable)
                dll_paths.append(os.path.join(base_path, "Dll2.dll"))
                dll_paths.append(os.path.join(base_path, "x64", "Debug", "Dll2.dll"))
                # Добавим поиск в текущем каталоге для EXE
                dll_paths.append("Dll2.dll")
            else:
                # Если запущено как Python скрипт
                base_path = os.path.dirname(os.path.abspath(__file__))
                parent_path = os.path.dirname(base_path)
                dll_paths.append(os.path.join(parent_path, "x64", "Debug", "Dll2.dll"))
                dll_paths.append(os.path.join(base_path, "Dll2.dll"))
            
            # Добавим системные пути в поиск
            dll_paths.append(os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'System32', 'Dll2.dll'))
            
            # Пробуем загрузить DLL из каждого возможного пути
            dll_loaded = False
            dll_path = None
            
            debug_print(f"Начинаем поиск DLL-файла в следующих путях: {dll_paths}")
            
            for path in dll_paths:
                debug_print(f"Проверка пути: {path}")
                if os.path.exists(path):
                    try:
                        debug_print(f"Попытка загрузки DLL из {path}")
                        self.process_dll = ctypes.WinDLL(path)
                        dll_path = path
                        dll_loaded = True
                        debug_print(f"DLL успешно загружена из {path}")
                        break
                    except Exception as e:
                        debug_print(f"Ошибка загрузки из {path}: {e}")
            
            if not dll_loaded:
                debug_print(f"DLL не найдена в путях: {dll_paths}")
                raise FileNotFoundError("DLL не найдена в доступных путях")
            
            debug_print(f"DLL успешно загружена из {dll_path}")
            
            # Определяем структуру ProcessInfo из DLL
            class ProcessInfoStruct(ctypes.Structure):
                _fields_ = [
                    ("processName", ctypes.c_wchar * 260),
                    ("cpuUsage", ctypes.c_double),
                    ("memoryUsage", ctypes.c_size_t),
                    ("diskReadRate", ctypes.c_double),
                    ("diskWriteRate", ctypes.c_double),
                    ("networkSent", ctypes.c_double),
                    ("networkReceived", ctypes.c_double)
                ]
            
            # Настраиваем функцию GetProcessInfo
            self.process_dll.GetProcessInfo.argtypes = [ctypes.c_ulong]
            self.process_dll.GetProcessInfo.restype = ProcessInfoStruct
            self.ProcessInfoStruct = ProcessInfoStruct
            self.use_dll = True
            debug_print("DLL успешно настроена и готова к использованию")
        except Exception as e:
            debug_print(f"Ошибка загрузки DLL: {e}")
            self.use_dll = False
            debug_print("Система будет работать без DLL, используя стандартные методы")
            
        self._prev_cpu_times = self._get_cpu_times()
        
        # Получаем начальные значения для дисковой и сетевой активности
        self._prev_disk_counters = self._get_disk_counters()
        self._prev_net_counters = self._get_network_counters()
        
    def _enable_debug_privilege(self):
        """Повышает привилегии процесса для доступа к системным процессам"""
        try:
            debug_print("Попытка получения привилегии SeDebugPrivilege...")
            
            # Получаем handle на kernel32.dll и advapi32.dll
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
            
            # Объявляем типы для функций
            OpenProcessToken = advapi32.OpenProcessToken
            OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
            OpenProcessToken.restype = wintypes.BOOL
            
            LookupPrivilegeValueW = advapi32.LookupPrivilegeValueW
            LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(LUID)]
            LookupPrivilegeValueW.restype = wintypes.BOOL
            
            AdjustTokenPrivileges = advapi32.AdjustTokenPrivileges
            AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES), 
                                           wintypes.DWORD, ctypes.POINTER(TOKEN_PRIVILEGES), ctypes.POINTER(wintypes.DWORD)]
            AdjustTokenPrivileges.restype = wintypes.BOOL
            
            # Открываем токен текущего процесса
            hToken = wintypes.HANDLE()
            if not OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(hToken)):
                debug_print(f"Не удалось открыть токен процесса: {ctypes.get_last_error()}")
                return False
            
            # Получаем LUID для привилегии SeDebugPrivilege
            luid = LUID()
            if not LookupPrivilegeValueW(None, SE_DEBUG_NAME, ctypes.byref(luid)):
                debug_print(f"Не удалось найти значение привилегии: {ctypes.get_last_error()}")
                kernel32.CloseHandle(hToken)
                return False
            
            # Подготавливаем структуру TOKEN_PRIVILEGES
            tp = TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            tp.Privileges[0].Luid = luid
            tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
            
            # Устанавливаем привилегию SeDebugPrivilege
            if not AdjustTokenPrivileges(hToken, False, ctypes.byref(tp), ctypes.sizeof(TOKEN_PRIVILEGES), None, None):
                debug_print(f"Не удалось настроить привилегии токена: {ctypes.get_last_error()}")
                kernel32.CloseHandle(hToken)
                return False
            
            error = ctypes.get_last_error()
            if error != 0:
                debug_print(f"AdjustTokenPrivileges успешно, но возможно не применены все запрашиваемые привилегии. Код ошибки: {error}")
            else:
                debug_print("SeDebugPrivilege успешно включена!")
            
            kernel32.CloseHandle(hToken)
            return True
        except Exception as e:
            debug_print(f"Ошибка при включении SeDebugPrivilege: {e}")
            return False
        
    def _setup_performance_counters(self):
        self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.psapi = ctypes.WinDLL('psapi', use_last_error=True)
        self.pdh = ctypes.WinDLL('pdh', use_last_error=True)
        
        # Настраиваем типы для функций
        self.kernel32.GetSystemTimes.argtypes = [
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME)
        ]
        self.kernel32.GetSystemTimes.restype = wintypes.BOOL
        
        self.kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
        self.kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
        
        # Добавляем определение для GetProcessMemoryInfo
        self.psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            wintypes.DWORD
        ]
        self.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

        # Добавляем определение для GetProcessIoCounters
        self.kernel32.GetProcessIoCounters.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(IO_COUNTERS)
        ]
        self.kernel32.GetProcessIoCounters.restype = wintypes.BOOL

    def _get_cpu_times(self) -> dict:
        idle_time = FILETIME()
        kernel_time = FILETIME()
        user_time = FILETIME()
        
        if not self.kernel32.GetSystemTimes(
            ctypes.byref(idle_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time)
        ):
            return {'idle': 0, 'kernel': 0, 'user': 0}
            
        return {
            'idle': (idle_time.dwHighDateTime << 32) | idle_time.dwLowDateTime,
            'kernel': (kernel_time.dwHighDateTime << 32) | kernel_time.dwLowDateTime,
            'user': (user_time.dwHighDateTime << 32) | user_time.dwLowDateTime
        }

    def get_cpu_usage(self) -> float:
        current = self._get_cpu_times()
        prev = self._prev_cpu_times
        
        idle_diff = current['idle'] - prev['idle']
        kernel_diff = current['kernel'] - prev['kernel']
        user_diff = current['user'] - prev['user']
        total_diff = kernel_diff + user_diff
        
        self._prev_cpu_times = current
        if total_diff > 0:
            return 100.0 * (1.0 - idle_diff / total_diff)
        return 0.0

    def get_memory_info(self) -> dict:
        meminfo = MEMORYSTATUSEX()
        meminfo.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        
        if not self.kernel32.GlobalMemoryStatusEx(ctypes.byref(meminfo)):
            return {'total': 0, 'available': 0, 'percent': 0}
            
        return {
            'total': meminfo.ullTotalPhys,
            'available': meminfo.ullAvailPhys,
            'percent': meminfo.dwMemoryLoad
        }

    def _get_disk_counters(self) -> dict:
        counters = {'read_bytes': 0, 'write_bytes': 0}
        try:
            # Используем Windows Performance Counters через ctypes
            query = ctypes.c_void_p()
            self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(query))
            
            counter_read = ctypes.c_void_p()
            counter_write = ctypes.c_void_p()
            
            self.pdh.PdhAddCounterW(
                query,
                "\\PhysicalDisk(_Total)\\Disk Read Bytes/sec",
                0,
                ctypes.byref(counter_read)
            )
            self.pdh.PdhAddCounterW(
                query,
                "\\PhysicalDisk(_Total)\\Disk Write Bytes/sec",
                0,
                ctypes.byref(counter_write)
            )
            
            self.pdh.PdhCollectQueryData(query)
            
            value = wintypes.DWORD()
            self.pdh.PdhGetFormattedCounterValue(
                counter_read,
                0x00000100,  # PDH_FMT_LONG
                None,
                ctypes.byref(value)
            )
            counters['read_bytes'] = value.value
            
            self.pdh.PdhGetFormattedCounterValue(
                counter_write,
                0x00000100,  # PDH_FMT_LONG
                None,
                ctypes.byref(value)
            )
            counters['write_bytes'] = value.value
            
            self.pdh.PdhCloseQuery(query)
        except Exception:
            pass
        return counters

    def _get_network_counters(self) -> dict:
        counters = {'bytes_sent': 0, 'bytes_recv': 0}
        try:
            query = ctypes.c_void_p()
            self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(query))
            
            counter_sent = ctypes.c_void_p()
            counter_recv = ctypes.c_void_p()
            
            self.pdh.PdhAddCounterW(
                query,
                "\\Network Interface(*)\\Bytes Sent/sec",
                0,
                ctypes.byref(counter_sent)
            )
            self.pdh.PdhAddCounterW(
                query,
                "\\Network Interface(*)\\Bytes Received/sec",
                0,
                ctypes.byref(counter_recv)
            )
            
            self.pdh.PdhCollectQueryData(query)
            
            value = wintypes.DWORD()
            self.pdh.PdhGetFormattedCounterValue(
                counter_sent,
                0x00000100,  # PDH_FMT_LONG
                None,
                ctypes.byref(value)
            )
            counters['bytes_sent'] = value.value
            
            self.pdh.PdhGetFormattedCounterValue(
                counter_recv,
                0x00000100,  # PDH_FMT_LONG
                None,
                ctypes.byref(value)
            )
            counters['bytes_recv'] = value.value
            
            self.pdh.PdhCloseQuery(query)
        except Exception:
            pass
        return counters

    def get_disk_io(self) -> dict:
        """Получает информацию о дисковой активности в МБ/с, используя данные DLL"""
        if self.use_dll and self.process_dll:
            try:
                # Получение данных напрямую из DLL для всех процессов
                total_read = 0.0
                total_write = 0.0
                processes = self.get_processes()
                
                # Суммируем данные о дисковой активности всех процессов
                for proc in processes:
                    total_read += proc.get('disk_read', 0.0)
                    total_write += proc.get('disk_write', 0.0)
                
                # Применяем масштабирование для лучшей видимости
                return {
                    'read_bytes': total_read,
                    'write_bytes': total_write
                }
            except Exception as e:
                debug_print(f"Ошибка при получении дисковой активности из DLL: {e}")
        
        # Если DLL не доступен или произошла ошибка, используем стандартный метод
        current_time = time.time()
        current = self._get_disk_counters()
        
        # Проверяем, есть ли предыдущие значения
        if not self._last_disk_counters:
            self._last_disk_counters = current
            self._last_disk_time = current_time
            return {'read_bytes': 0.5, 'write_bytes': 0.5}
        
        # Вычисляем разницу во времени
        time_diff = current_time - self._last_disk_time
        if time_diff < 0.1:
            time_diff = 0.1
        
        # Вычисляем скорость в МБ/с
        read_diff = current['read_bytes'] - self._last_disk_counters['read_bytes']
        write_diff = current['write_bytes'] - self._last_disk_counters['write_bytes']
        
        read_speed = (read_diff / time_diff) / (1024 * 1024)
        write_speed = (write_diff / time_diff) / (1024 * 1024)
        
        # Обновляем предыдущие значения
        self._last_disk_counters = current
        self._last_disk_time = current_time
        
        return {'read_bytes': read_speed, 'write_bytes': write_speed}
        
    def get_network_io(self) -> dict:
        """Получает информацию о сетевой активности в МБ/с, используя данные DLL"""
        if self.use_dll and self.process_dll:
            try:
                # Получение данных напрямую из DLL для всех процессов
                total_sent = 0.0
                total_recv = 0.0
                processes = self.get_processes()
                
                # Суммируем данные о сетевой активности всех процессов
                for proc in processes:
                    total_sent += proc.get('network_sent', 0.0)
                    total_recv += proc.get('network_recv', 0.0)
                
                # Применяем масштабирование для лучшей видимости
                return {
                    'bytes_sent': total_sent,
                    'bytes_recv': total_recv
                }
            except Exception as e:
                debug_print(f"Ошибка при получении сетевой активности из DLL: {e}")
        
        # Если DLL не доступен или произошла ошибка, используем стандартный метод
        current_time = time.time()
        current = self._get_network_counters()
        
        # Проверяем, есть ли предыдущие значения
        if not self._last_network_counters:
            self._last_network_counters = current
            self._last_network_time = current_time
            return {'bytes_sent': 0.5, 'bytes_recv': 0.5}
        
        # Вычисляем разницу во времени
        time_diff = current_time - self._last_network_time
        if time_diff < 0.1:
            time_diff = 0.1
        
        # Вычисляем скорость в МБ/с
        sent_diff = current['bytes_sent'] - self._last_network_counters['bytes_sent']
        recv_diff = current['bytes_recv'] - self._last_network_counters['bytes_recv']
        
        sent_speed = (sent_diff / time_diff) / (1024 * 1024)
        recv_speed = (recv_diff / time_diff) / (1024 * 1024)
        
        # Обновляем предыдущие значения
        self._last_network_counters = current
        self._last_network_time = current_time
        
        return {'bytes_sent': sent_speed, 'bytes_recv': recv_speed}

    def get_cpu_freq(self) -> dict:
        try:
            # Используем Windows Management API через ctypes
            freq = ctypes.c_uint64()
            if self.kernel32.QueryPerformanceFrequency(ctypes.byref(freq)):
                return {'current': freq.value / 1000000.0}  # Конвертируем в MHz
        except Exception:
            pass
        return {'current': 0.0}

    def get_boot_time(self) -> float:
        try:
            return self.kernel32.GetTickCount64() / 1000.0
        except Exception:
            return time.time()

    def get_process_io_counters(self, handle, pid) -> dict:
        """Получает информацию о дисковой и сетевой активности процесса"""
        io = IO_COUNTERS()
        current_time = time.time()
        
        if not self.kernel32.GetProcessIoCounters(handle, ctypes.byref(io)):
            return {
                'disk_read': 0.0,
                'disk_write': 0.0,
                'network_sent': 0.0,
                'network_recv': 0.0
            }
            
        current_io = {
            'time': current_time,
            'read': io.ReadTransferCount,
            'write': io.WriteTransferCount,
            'other': io.OtherTransferCount
        }
        
        if pid in self._process_io:
            prev_io = self._process_io[pid]
            time_diff = current_time - prev_io['time']
            
            if time_diff > 0:
                # Конвертируем байты в мегабайты и применяем масштабирование
                disk_scale = 2.5  # Коэффициент для диска
                net_scale = 2.0   # Коэффициент для сети
                
                disk_read = (current_io['read'] - prev_io['read']) / (time_diff * 1024 * 1024) * disk_scale
                disk_write = (current_io['write'] - prev_io['write']) / (time_diff * 1024 * 1024) * disk_scale
                
                # Разделяем сетевой трафик поровну между отправкой и получением
                network_total = (current_io['other'] - prev_io['other']) / (time_diff * 1024 * 1024) * net_scale
                network_sent = network_total * 0.4  # 40% на исходящий трафик
                network_recv = network_total * 0.6  # 60% на входящий трафик
                
                self._process_io[pid] = current_io
                return {
                    'disk_read': disk_read,
                    'disk_write': disk_write,
                    'network_sent': network_sent,
                    'network_recv': network_recv
                }
        
        self._process_io[pid] = current_io
        return {
            'disk_read': 0.0,
            'disk_write': 0.0,
            'network_sent': 0.0,
            'network_recv': 0.0
        }

    def get_processes(self) -> list:
        """Получает список процессов с дополнительной информацией от DLL"""
        processes = []
        
        try:
            debug_print("Повышаем привилегии перед получением процессов")
            self._enable_debug_privilege()
            
            debug_print("Получаем список процессов")
            # Если DLL доступен и загружен, используем её для получения информации о процессах
            if self.use_dll and self.process_dll:
                try:
                    # Получаем список всех процессов
                    process_ids = (wintypes.DWORD * 4096)()
                    cb_needed = wintypes.DWORD()
                    
                    if not self.psapi.EnumProcesses(
                        ctypes.byref(process_ids),
                        ctypes.sizeof(process_ids),
                        ctypes.byref(cb_needed)
                    ):
                        debug_print("Не удалось перечислить процессы")
                        # Продолжаем со стандартной реализацией
                    else:
                        # Количество возвращенных процессов
                        num_processes = cb_needed.value // ctypes.sizeof(wintypes.DWORD)
                        debug_print(f"Найдено {num_processes} процессов")
                        
                        for i in range(num_processes):
                            pid = process_ids[i]
                            if pid <= 0:
                                continue
                            
                            try:
                                # Получаем информацию из DLL
                                proc_info = self.process_dll.GetProcessInfo(pid)
                                
                                # Проверяем, что имя процесса не пустое и процесс существует
                                if proc_info.processName and proc_info.processName != "":
                                    processes.append({
                                        'pid': pid,
                                        'name': proc_info.processName,
                                        'cpu_percent': proc_info.cpuUsage,
                                        'memory_info': {
                                            'rss': proc_info.memoryUsage
                                        },
                                        'disk_read': proc_info.diskReadRate,
                                        'disk_write': proc_info.diskWriteRate,
                                        'network_sent': proc_info.networkSent,
                                        'network_recv': proc_info.networkReceived,
                                        # Добавляем флаг для системных процессов
                                        'is_system': pid < 100 or proc_info.processName.lower() in ['system', 'registry', 'smss.exe', 'csrss.exe', 'wininit.exe', 'services.exe']
                                    })
                            except Exception as e:
                                debug_print(f"Ошибка получения информации о процессе {pid}: {e}")
                                continue
                        
                        if len(processes) > 0:
                            debug_print(f"Успешно получено {len(processes)} процессов через DLL")
                            return processes
                        else:
                            debug_print("DLL не вернула процессы, используем Python-метод")
                except Exception as e:
                    debug_print(f"Ошибка получения процессов через DLL: {e}")
                    # Если DLL метод не сработал, продолжаем стандартным способом
            
            # Стандартный метод через psutil
            debug_print("Используем psutil для получения процессов")
            ps_processes = []
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'io_counters', 'username']):
                try:
                    # Получаем основную информацию
                    proc_info = proc.info
                    process_data = {
                        'pid': proc_info['pid'],
                        'name': proc_info['name'],
                        'cpu_percent': proc_info['cpu_percent'],
                        'memory_info': proc_info['memory_info']._asdict() if proc_info['memory_info'] else {'rss': 0},
                        'username': proc_info.get('username', 'N/A'),
                        'is_system': proc_info['pid'] < 100 or proc_info['name'].lower() in ['system', 'registry', 'smss.exe', 'csrss.exe', 'wininit.exe', 'services.exe']
                    }
                    
                    # Получаем данные I/O
                    io_counters = proc_info.get('io_counters')
                    if io_counters:
                        # Получаем текущее время
                        current_time = time.time()
                        
                        # Проверяем, есть ли предыдущие значения
                        if proc_info['pid'] in self._process_io:
                            prev_io = self._process_io[proc_info['pid']]['counters']
                            prev_time = self._process_io[proc_info['pid']]['time']
                            
                            # Вычисляем разницу во времени
                            time_diff = current_time - prev_time
                            if time_diff > 0:
                                # Масштабирующие коэффициенты
                                disk_scale = 1.0
                                net_scale = 1.0
                                
                                # Получаем текущие значения счетчиков
                                read_bytes = io_counters.read_bytes
                                write_bytes = io_counters.write_bytes
                                other_bytes = io_counters.other_bytes if hasattr(io_counters, 'other_bytes') else 0
                                
                                # Вычисляем скорость
                                read_rate = (read_bytes - prev_io['read_bytes']) / (time_diff * 1024 * 1024) * disk_scale
                                write_rate = (write_bytes - prev_io['write_bytes']) / (time_diff * 1024 * 1024) * disk_scale
                                net_rate = (other_bytes - prev_io['other_bytes']) / (time_diff * 1024 * 1024) * net_scale if hasattr(io_counters, 'other_bytes') else 0
                                
                                # Добавляем данные в словарь
                                process_data['disk_read'] = read_rate
                                process_data['disk_write'] = write_rate
                                process_data['network_sent'] = net_rate * 0.4  # 40% на исходящий трафик
                                process_data['network_recv'] = net_rate * 0.6  # 60% на входящий трафик
                        
                        # Обновляем сохраненные значения
                        self._process_io[proc_info['pid']] = {
                            'counters': {
                                'read_bytes': io_counters.read_bytes,
                                'write_bytes': io_counters.write_bytes,
                                'other_bytes': io_counters.other_bytes if hasattr(io_counters, 'other_bytes') else 0
                            },
                            'time': current_time
                        }
                    
                    ps_processes.append(process_data)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                except Exception as e:
                    debug_print(f"Ошибка обработки процесса через psutil: {e}")
                    continue
            
            debug_print(f"Получено {len(ps_processes)} процессов через psutil")
            processes = ps_processes
        except Exception as e:
            debug_print(f"Общая ошибка в get_processes: {e}")
        
        return processes

class DataCollector(QThread):
    data_updated = pyqtSignal(dict)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_flag = threading.Event()
        self.interval = 1.0
        self._cache = {}
        self._process_cache = {}
        self._cache_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=4)
        self._last_full_update = 0
        self._full_update_interval = 5.0
        self.metrics = SystemMetrics()
        
    def run(self):
        while not self._stop_flag.is_set():
            try:
                system_info = self.collect_system_info()
                self.data_updated.emit(system_info)
                time.sleep(max(0, self.interval - (time.time() % self.interval)))
            except Exception:
                continue
                
    def stop(self):
        self._stop_flag.set()
        self.executor.shutdown(wait=False)
        
    def collect_system_info(self) -> dict:
        current_time = time.time()
        
        with self._cache_lock:
            info = {
                'cpu_percent': self.metrics.get_cpu_usage(),
                'memory': self.metrics.get_memory_info(),
                'disk': self.metrics.get_disk_io(),
                'network': self.metrics.get_network_io(),
                'cpu_freq': self.metrics.get_cpu_freq(),
                'boot_time': self.metrics.get_boot_time(),
                'last_update': current_time,
                'processes': self.metrics.get_processes()  # Теперь это список процессов
            }
            
            self._cache = info
            return info

class NumericTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        try:
            return float(self.data(Qt.UserRole)) < float(other.data(Qt.UserRole))
        except (ValueError, TypeError):
            return self.text() < other.text()

class PerformanceTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_lock = threading.Lock()
        self._update_lock = threading.Lock()
        self.is_dark_theme = parent.is_dark_theme if parent else False
        self.init_data()
        self.init_ui()
        
    def init_data(self):
        # Используем deque для эффективного управления размером списка
        self.values = {
            metric: deque(maxlen=60) 
            for metric in ['cpu', 'memory', 'disk', 'network']
        }
        self.current_metric = 'cpu'
        self._prev_values = {}
        self._last_update = 0
        self._update_interval = 0.5  # Обновление графика каждые 0.5 секунды
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Главный горизонтальный layout
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Левая панель с кнопками
        left_panel = QWidget()
        left_panel.setObjectName("left_panel")
        left_panel.setFixedWidth(200)
        
        # Устанавливаем цвет фона левой панели всегда темным
        left_panel.setStyleSheet("""
            QWidget#left_panel {
                background-color: #1e1e1e;
            }
        """)
        
        left_panel_layout = QVBoxLayout(left_panel)
        left_panel_layout.setContentsMargins(0, 0, 0, 0)
        left_panel_layout.setSpacing(0)
        
        # Создаем кнопки для переключения метрик
        self.metric_buttons = {}
        metrics = {
            'cpu': ('ЦП', '#094771'),
            'memory': ('Память', '#772940'),
            'disk': ('Диск', '#2d5a27'),
            'network': ('Ethernet', '#775209')
        }
        
        for metric, (label, hover_color) in metrics.items():
            btn = QPushButton(label)
            btn.setFixedHeight(40)
            btn.setCheckable(True)
            btn.setFont(QFont('Segoe UI', 9))
            btn.clicked.connect(lambda checked, m=metric: self.switch_metric(m))
            btn.setStyleSheet(f"""
                QPushButton {{
                    text-align: left;
                    padding: 10px;
                    border: none;
                    background-color: #1e1e1e;
                    color: #ffffff;
                }}
                QPushButton:checked {{
                    background-color: {hover_color};
                }}
                QPushButton:hover:!checked {{
                    background-color: {hover_color};
                    opacity: 0.8;
                }}
            """)
            self.metric_buttons[metric] = btn
            left_panel_layout.addWidget(btn)
        
        left_panel_layout.addStretch()
        
        # Правая панель с графиком
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 10, 10, 10)
        
        # График
        self.chart = QChart()
        self.chart.setAnimationOptions(QChart.NoAnimation)
        self.chart.setBackgroundVisible(False)
        self.chart.legend().hide()
        self.chart.setTitle("ЦП")
        title_font = QFont('Segoe UI', 20)
        title_font.setBold(True)
        self.chart.setTitleFont(title_font)
        self.chart.setTitleBrush(QColor("#ffffff" if self.is_dark_theme else "#000000"))
        
        # Серия данных
        self.series = QLineSeries()
        pen = self.series.pen()
        pen.setWidth(2)
        pen.setColor(QColor("#3794ff"))
        self.series.setPen(pen)
        self.chart.addSeries(self.series)
        
        # Настройка осей
        self.axis_x = QValueAxis()
        self.axis_x.setRange(0, 60)
        self.axis_x.setVisible(True)
        self.axis_x.setLabelsVisible(True)
        self.axis_x.setGridLineVisible(True)
        self.axis_x.setMinorGridLineVisible(False)
        self.axis_x.setTitleText("Время (с)")
        self.axis_x.setLabelFormat("%d")
        
        self.axis_y = QValueAxis()
        self.axis_y.setRange(0, 100)
        self.axis_y.setVisible(True)
        self.axis_y.setLabelsVisible(True)
        self.axis_y.setGridLineVisible(True)
        self.axis_y.setMinorGridLineVisible(False)
        self.axis_y.setLabelFormat("%.1f")
        
        # Настройка цветов осей для темной темы
        grid_color = QColor("#333333" if self.is_dark_theme else "#e0e0e0")
        self.axis_x.setGridLineColor(grid_color)
        self.axis_y.setGridLineColor(grid_color)
        self.axis_x.setLabelsColor(QColor("#808080" if self.is_dark_theme else "#666666"))
        self.axis_y.setLabelsColor(QColor("#808080" if self.is_dark_theme else "#666666"))
        
        self.chart.addAxis(self.axis_x, Qt.AlignBottom)
        self.chart.addAxis(self.axis_y, Qt.AlignLeft)
        self.series.attachAxis(self.axis_x)
        self.series.attachAxis(self.axis_y)
        
        # Виджет графика
        chart_view = QChartView(self.chart)
        chart_view.setRenderHint(QPainter.Antialiasing)
        right_layout.addWidget(chart_view)
        
        # Информационные метки
        info_widget = QWidget()
        self.info_layout = QGridLayout(info_widget)  # Сохраняем ссылку на layout
        self.info_layout.setContentsMargins(10, 10, 10, 10)
        self.info_layout.setSpacing(10)
        
        # Создаем метки в три колонки
        labels = [
            ("Использование", "19%"),
            ("Скорость", "2,79"),
            ("Процессы", "184"),
            ("Потоки", "2010"),
            ("Дескрипторы", "74108"),
            ("Время работы", "0:05:46:41")
        ]
        
        self.info_labels = {}  # Словарь для хранения меток
        for i, (label, value) in enumerate(labels):
            row = i // 3
            col = i % 3
            
            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(5)
            
            value_label = QLabel(value)
            value_label.setFont(QFont('Segoe UI', 11))
            name_label = QLabel(label)
            name_label.setFont(QFont('Segoe UI', 9))
            name_label.setStyleSheet("color: #666666;")
            
            container_layout.addWidget(value_label)
            container_layout.addWidget(name_label)
            
            self.info_layout.addWidget(container, row, col)
            self.info_labels[label] = value_label  # Сохраняем ссылку на метку
        
        right_layout.addWidget(info_widget)
        
        # Добавляем панели в главный layout
        main_layout.addWidget(left_panel)
        main_layout.addWidget(right_panel, 1)
        
        layout.addLayout(main_layout)
        
        # Активируем первую кнопку
        self.metric_buttons['cpu'].setChecked(True)

    def switch_metric(self, metric):
        self.current_metric = metric
        for m, btn in self.metric_buttons.items():
            btn.setChecked(m == metric)
        
        # Обновляем цвет графика
        colors = {
            'cpu': '#3794ff',
            'memory': '#ff4a4a',
            'disk': '#4aff4a',
            'network': '#ffd700'
        }
        
        pen = self.series.pen()
        pen.setColor(QColor(colors[metric]))
        pen.setWidth(2)  # Увеличиваем толщину линии для лучшей видимости
        self.series.setPen(pen)
        
        # Обновляем заголовок и метки
        titles = {
            'cpu': ('ЦП', '%'),
            'memory': ('Память', '%'),
            'disk': ('Диск', 'МБ/с'),
            'network': ('Ethernet', 'МБ/с')
        }
        title, y_label = titles[metric]
        self.chart.setTitle(title)
        self.axis_y.setTitleText(y_label)
        
        # Устанавливаем диапазон оси Y в зависимости от метрики
        if metric in ['cpu', 'memory']:
            self.axis_y.setRange(0, 100)
        else:
            # Начальный диапазон для диска и сети
            values = list(self.values[metric])
            if values:
                max_value = max(values)
                if max_value > 5:
                    # Если есть большие значения, устанавливаем диапазон под них
                    self.axis_y.setRange(0, max(max_value * 1.2, 10))
                else:
                    # Для маленьких значений используем фиксированный диапазон
                    self.axis_y.setRange(0, 5)
            else:
                self.axis_y.setRange(0, 5)
        
        # Обновляем данные графика
        self.update_chart()

    def update_chart(self):
        with self._update_lock:
            self.series.clear()
            values = list(self.values[self.current_metric])
            if not values:
                return
            
            # Масштабируем значения только если они очень маленькие
            if self.current_metric in ['disk', 'network']:
                # Находим максимальное значение для определения диапазона
                max_value = max(values)
                
                # Если максимальное значение слишком маленькое, масштабируем график
                if max_value < 0.1:
                    if max_value <= 0:
                        # Если нет активности, показываем прямую линию на минимальном уровне
                        values = [0.1] * len(values)
                    else:
                        # Масштабируем маленькие значения для видимости
                        scale = 0.5 / max_value
                        values = [v * scale for v in values]
                
                # Если значение превышает диапазон, пересчитываем шкалу
                if max_value > self.axis_y.max():
                    new_max = max(max_value * 1.2, 5)
                    self.axis_y.setRange(0, new_max)
            
            # Добавляем точки на график
            points = []
            for i, v in enumerate(values):
                points.append(QPointF(i, v))
            
            # Устанавливаем новые точки
            self.series.replace(points)

    def update_data(self, system_info: dict):
        current_time = time.time()
        
        # Проверяем интервал обновления
        if current_time - self._last_update < self._update_interval:
            return
            
        with self._data_lock:
            metrics_data = self.calculate_metrics(system_info)
            
            # Обновляем значения для всех метрик
            for metric, value in metrics_data.items():
                self.values[metric].append(value)
            
            # Обновляем график если это текущая метрика
            if self.current_metric in metrics_data:
                self.update_chart()
                
            self.update_labels(system_info, metrics_data)
            self._last_update = current_time
            
    def calculate_metrics(self, system_info: dict) -> dict:
        metrics = {}
        
        # CPU
        metrics['cpu'] = system_info.get('cpu_percent', 0.0)
        
        # Память
        memory = system_info.get('memory', {})
        total_memory = memory.get('total', 1)
        used_memory = total_memory - memory.get('available', 0)
        metrics['memory'] = (used_memory / total_memory) * 100 if total_memory > 0 else 0.0
        
        # Диск
        disk_info = system_info.get('disk', {})
        read_bytes = disk_info.get('read_bytes', 0)
        write_bytes = disk_info.get('write_bytes', 0)
        disk_total = read_bytes + write_bytes
        
        # Применяем масштабирование только в случае, если значения слишком маленькие
        if disk_total < 0.1:
            metrics['disk'] = 0.1  # Минимальное значение для видимости
        else:
            # Если активность есть, показываем её без искусственного увеличения
            metrics['disk'] = disk_total
        
        # Сеть
        net_info = system_info.get('network', {})
        net_sent = net_info.get('bytes_sent', 0)
        net_recv = net_info.get('bytes_recv', 0)
        net_total = net_sent + net_recv
        
        # Аналогично для сети
        if net_total < 0.1:
            metrics['network'] = 0.1  # Минимальное значение для видимости
        else:
            # Если активность есть, показываем её без искусственного увеличения
            metrics['network'] = net_total
        
        return metrics

    def update_labels(self, system_info: dict, metrics_data: dict):
        with self._update_lock:
            # Обновляем метки
            new_values = {
                'Использование': f"{metrics_data.get('cpu', 0):.1f}%",
                'Скорость': (
                    f"{system_info.get('cpu_freq', {}).get('current', 0) / 1000:.1f} GHz"
                    if system_info.get('cpu_freq') else "N/A"
                ),
                'Процессы': str(len(system_info.get('processes', []))),
                'Потоки': str(sum(1 for p in system_info.get('processes', []))),
                'Дескрипторы': str(sum(1 for p in system_info.get('processes', []))),
                'Время работы': self._format_uptime(system_info.get('boot_time', 0))
            }
            
            # Обновляем только изменившиеся значения
            for label, value in new_values.items():
                if label in self.info_labels:
                    current = self.info_labels[label].text()
                    if current != value:
                        self.info_labels[label].setText(value)

    def _format_uptime(self, boot_time):
        if not boot_time:
            return "00:00:00"
        uptime = datetime.now() - datetime.fromtimestamp(boot_time)
        total_seconds = int(uptime.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def update_theme(self, is_dark):
        self.is_dark_theme = is_dark
        
        # Обновляем цвет заголовка графика
        self.chart.setTitleBrush(QColor("#ffffff" if is_dark else "#000000"))
        
        # Обновляем стили кнопок (сохраняем темный фон)
        metrics = {
            'cpu': ('ЦП', '#094771'),
            'memory': ('Память', '#772940'),
            'disk': ('Диск', '#2d5a27'),
            'network': ('Ethernet', '#775209')
        }
        
        for metric, (label, hover_color) in metrics.items():
            if metric in self.metric_buttons:
                self.metric_buttons[metric].setStyleSheet(f"""
                    QPushButton {{
                        text-align: left;
                        padding: 10px;
                        border: none;
                        background-color: #1e1e1e;
                        color: #ffffff;
                    }}
                    QPushButton:checked {{
                        background-color: {hover_color};
                    }}
                    QPushButton:hover:!checked {{
                        background-color: {hover_color};
                        opacity: 0.8;
                    }}
                """)
        
        # Обновляем цвета графика
        grid_color = QColor("#333333" if is_dark else "#e0e0e0")
        label_color = QColor("#808080" if is_dark else "#666666")
        
        self.axis_x.setGridLineColor(grid_color)
        self.axis_y.setGridLineColor(grid_color)
        self.axis_x.setLabelsColor(label_color)
        self.axis_y.setLabelsColor(label_color)
        
        # Обновляем цвета информационных меток
        for label in self.info_labels.values():
            label.setStyleSheet(f"color: {'#ffffff' if is_dark else '#000000'};")
            
        # Принудительно обновляем виджет
        self.repaint()

class UsersTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
        self.prev_disk_bytes = {}
        self.prev_net_bytes = {}
        self.last_update = time.time()
        self.user_cache = {}
        self.username_cache = {}
        self.metrics = SystemMetrics()
        self._current_username = getpass.getuser()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Создаем таблицу пользователей
        self.table = QTableWidget()
        self.table.setFont(QFont('Segoe UI', 9))
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Пользователь", "ЦП", "Память", "Диск", "Сеть"
        ])

        # Настройка таблицы
        header = self.table.horizontalHeader()
        header.setFont(QFont('Segoe UI', 9))
        for i in range(5):
            header.setSectionResizeMode(i, QHeaderView.Stretch)

        layout.addWidget(self.table)

    def get_process_username(self, pid):
        return self._current_username

    def update_data(self, system_info):
        current_time = time.time()
        if current_time - self.last_update < 1.0:  # Обновляем раз в секунду
            return

        self.last_update = current_time
        
        # Инициализируем статистику пользователя
        user_stats = {
            self._current_username: {
                'cpu': 0.0,
                'memory': 0,
                'disk': 0.0,
                'network': 0.0
            }
        }

        # Собираем данные со всех процессов
        total_disk_read = 0.0
        total_disk_write = 0.0
        total_net_sent = 0.0
        total_net_recv = 0.0
        
        for proc_info in system_info.get('processes', []):
            try:
                # Суммируем CPU и память
                user_stats[self._current_username]['cpu'] += proc_info.get('cpu_percent', 0)
                user_stats[self._current_username]['memory'] += proc_info.get('memory_info', {}).get('rss', 0)
                
                # Суммируем диск и сеть
                total_disk_read += proc_info.get('disk_read', 0.0)
                total_disk_write += proc_info.get('disk_write', 0.0)
                total_net_sent += proc_info.get('network_sent', 0.0)
                total_net_recv += proc_info.get('network_recv', 0.0)
            except Exception:
                continue

        # Конвертируем память в МБ
        user_stats[self._current_username]['memory'] /= (1024 * 1024)
        
        # Общие значения диска и сети
        disk_total = total_disk_read + total_disk_write
        net_total = total_net_sent + total_net_recv
        
        user_stats[self._current_username]['disk'] = disk_total
        user_stats[self._current_username]['network'] = net_total

        # Обновляем таблицу
        self.update_table(user_stats)

    def update_table(self, user_stats):
        self.table.setRowCount(len(user_stats))
        for row, (username, stats) in enumerate(user_stats.items()):
            items = [
                (0, username),
                (1, f"{stats['cpu']:.1f}%"),
                (2, f"{stats['memory']:.1f} МБ"),
                (3, f"{stats['disk']:.1f} МБ/с"),
                (4, f"{stats['network']:.1f} МБ/с")
            ]
            
            for col, value in items:
                item = self.table.item(row, col)
                if item is None:
                    item = QTableWidgetItem(value)
                    self.table.setItem(row, col, item)
                elif item.text() != value:
                    item.setText(value)

class TaskManagerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.is_dark_theme = False
        self.data_collector = None
        
        # Попытка повысить привилегии для работы со всеми процессами в системе
        # Это особенно важно для EXE-версии
        self.elevate_privileges()
        
        # Инициализируем UI
        self.init_ui()
        
        # Настройка сборщика данных
        self.setup_collector()
        
        # Установка таймера для обновления заголовка с количеством процессов
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_window_title)
        self.update_timer.start(2000)  # Обновление каждые 2 секунды
        
    def elevate_privileges(self):
        """Повышает привилегии приложения для доступа к системным процессам"""
        try:
            # Проверяем, запущены ли мы с правами администратора
            if not self.is_admin():
                debug_print("Приложение не запущено с правами администратора. Некоторые процессы могут быть недоступны.")
            else:
                debug_print("Приложение запущено с правами администратора.")
                
            # Попытка включить привилегию отладки для доступа к системным процессам
            SE_DEBUG_NAME = "SeDebugPrivilege"
            privilege_id = win32security.LookupPrivilegeValue(None, SE_DEBUG_NAME)
            
            # Открываем токен процесса
            hProcess = win32api.GetCurrentProcess()
            hToken = win32security.OpenProcessToken(
                hProcess, 
                win32security.TOKEN_ADJUST_PRIVILEGES | win32security.TOKEN_QUERY
            )
            
            # Включаем привилегию
            new_privileges = [(privilege_id, win32security.SE_PRIVILEGE_ENABLED)]
            win32security.AdjustTokenPrivileges(hToken, 0, new_privileges)
            
            debug_print("Привилегия SeDebugPrivilege успешно включена.")
        except Exception as e:
            debug_print(f"Ошибка при повышении привилегий: {e}")
    
    def is_admin(self):
        """Проверяет, запущено ли приложение с правами администратора"""
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except Exception as e:
            debug_print(f"Ошибка при проверке прав администратора: {e}")
            return False
            
    def update_window_title(self):
        """Обновляет заголовок окна с количеством процессов"""
        try:
            process_count = len(self.performance_tab.processes_tab.processes)
            self.setWindowTitle(f"Диспетчер задач - {process_count} процессов")
        except:
            self.setWindowTitle("Диспетчер задач")
        
    def setup_collector(self):
        self.data_collector = DataCollector(self)
        self.data_collector.data_updated.connect(self.update_data)
        self.data_collector.start()
        
    def closeEvent(self, event):
        self.data_collector.stop()
        super().closeEvent(event)
        
    def init_ui(self):
        self.setWindowTitle("Диспетчер задач")
        self.setGeometry(100, 100, 1000, 600)
        self.sort_column = 0
        self.sort_order = Qt.AscendingOrder

        # Создание центрального виджета
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Создание вкладок
        self.tab_widget = QTabWidget()
        self.tab_widget.setFont(QFont('Segoe UI', 10))
        
        # Вкладка процессов
        process_tab = QWidget()
        process_layout = QVBoxLayout(process_tab)

        # Создание таблицы
        self.table = QTableWidget()
        self.table.setFont(QFont('Segoe UI', 9))
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Имя", "ЦП", "Память", "Диск", "Сеть"
        ])
        
        # Настройка таблицы
        header = self.table.horizontalHeader()
        header.sectionClicked.connect(self.on_header_clicked)
        header.setFont(QFont('Segoe UI', 9))
        for i in range(5):
            header.setSectionResizeMode(i, QHeaderView.Stretch)
        
        # Нижняя панель с кнопками
        bottom_panel = QWidget()
        bottom_layout = QHBoxLayout(bottom_panel)
        
        # Кнопка смены темы
        self.theme_button = QPushButton("🌙 Темная тема")
        self.theme_button.setFont(QFont('Segoe UI', 9))
        self.theme_button.clicked.connect(self.toggle_theme)
        
        # Кнопка "Снять задачу"
        kill_button = QPushButton("Снять задачу")
        kill_button.setFont(QFont('Segoe UI', 9))
        kill_button.clicked.connect(self.kill_selected_process)
        
        bottom_layout.addWidget(self.theme_button)
        bottom_layout.addStretch()
        bottom_layout.addWidget(kill_button)
        
        process_layout.addWidget(self.table)
        process_layout.addWidget(bottom_panel)
        
        # Вкладка производительности
        self.performance_tab = PerformanceTab(self)
        
        # Вкладка пользователей
        self.users_tab = UsersTab()
        
        # Добавление вкладок
        self.tab_widget.addTab(process_tab, "ПРОЦЕССЫ")
        self.tab_widget.addTab(self.performance_tab, "ПРОИЗВОДИТЕЛЬНОСТЬ")
        self.tab_widget.addTab(self.users_tab, "ПОЛЬЗОВАТЕЛИ")
        
        main_layout.addWidget(self.tab_widget)

        # Применяем тему
        self.apply_theme()
        
        # Инициализируем пустую таблицу
        self.table.setRowCount(0)

    def toggle_theme(self):
        self.is_dark_theme = not self.is_dark_theme
        self.theme_button.setText("☀️" if self.is_dark_theme else "🌙")
        self.apply_theme()

    def apply_theme(self):
        if self.is_dark_theme:
            self.theme_button.setText("☀️")
            # Устанавливаем темный фон для всего приложения
            app = QApplication.instance()
            app.setStyleSheet("""
                QMainWindow, QWidget, QTabWidget, QTabBar {
                    background-color: #1e1e1e;
                }
            """)
            self.setStyleSheet("""
                QMainWindow, QWidget {
                    background-color: #1e1e1e;
                    color: #ffffff;
                }
                QTableWidget {
                    background-color: #1e1e1e;
                    color: #ffffff;
                    gridline-color: #333333;
                    border: none;
                }
                QTableWidget::item {
                    padding: 5px;
                    border-bottom: 1px solid #333333;
                }
                QTableWidget::item:selected {
                    background-color: #094771;
                    color: #ffffff;
                }
                QHeaderView::section {
                    background-color: #1e1e1e;
                    color: #ffffff;
                    padding: 5px;
                    border: none;
                    border-right: 1px solid #333333;
                    border-bottom: 1px solid #333333;
                }
                QPushButton {
                    background-color: #1e1e1e;
                    color: #ffffff;
                    border: 1px solid #3d3d3d;
                    padding: 5px 10px;
                    border-radius: 2px;
                }
                QPushButton:hover {
                    background-color: #3d3d3d;
                }
                QTabWidget::pane {
                    border-top: 1px solid #333333;
                }
                QTabBar::tab {
                    background-color: #1e1e1e;
                    color: #ffffff;
                    border: none;
                    padding: 8px 20px;
                    min-width: 150px;
                }
                QTabBar::tab:selected {
                    background-color: #1e1e1e;
                    border-top: 1px solid #333333;
                    border-right: 1px solid #333333;
                    border-left: 1px solid #333333;
                }
                QTabBar::tab:hover:!selected {
                    background-color: #3d3d3d;
                }
            """)
            
            # Обновляем тему для вкладки производительности
            self.performance_tab.update_theme(True)
            
        else:
            self.theme_button.setText("🌙")
            # Устанавливаем светлый фон для всего приложения
            app = QApplication.instance()
            app.setStyleSheet("""
                QMainWindow, QWidget, QTabWidget, QTabBar {
                    background-color: #ffffff;
                }
            """)
            self.setStyleSheet("""
                QMainWindow, QWidget {
                    background-color: #ffffff;
                    color: #000000;
                }
                QTableWidget {
                    background-color: #ffffff;
                    color: #000000;
                    gridline-color: #e0e0e0;
                    border: none;
                }
                QTableWidget::item {
                    padding: 5px;
                    border-bottom: 1px solid #e0e0e0;
                }
                QTableWidget::item:selected {
                    background-color: #cce8ff;
                    color: #000000;
                }
                QHeaderView::section {
                    background-color: #f5f5f5;
                    color: #000000;
                    padding: 5px;
                    border: none;
                    border-right: 1px solid #e0e0e0;
                    border-bottom: 1px solid #e0e0e0;
                }
                QPushButton {
                    background-color: #ffffff;
                    color: #000000;
                    border: 1px solid #e0e0e0;
                    padding: 5px 10px;
                    border-radius: 2px;
                }
                QPushButton:hover {
                    background-color: #f5f5f5;
                }
                QTabWidget::pane {
                    border-top: 1px solid #e0e0e0;
                }
                QTabBar::tab {
                    background-color: #f5f5f5;
                    color: #000000;
                    border: none;
                    padding: 8px 20px;
                    min-width: 150px;
                }
                QTabBar::tab:selected {
                    background-color: #ffffff;
                    border-top: 1px solid #e0e0e0;
                    border-right: 1px solid #e0e0e0;
                    border-left: 1px solid #e0e0e0;
                }
                QTabBar::tab:hover:!selected {
                    background-color: #e0e0e0;
                }
            """)
            
            # Обновляем тему для вкладки производительности
            self.performance_tab.update_theme(False)
            
        # Принудительно обновляем все виджеты
        self.repaint()
        self.tab_widget.repaint()
        self.performance_tab.repaint()

    def on_header_clicked(self, logical_index):
        if self.sort_column == logical_index:
            self.sort_order = Qt.DescendingOrder if self.sort_order == Qt.AscendingOrder else Qt.AscendingOrder
        else:
            self.sort_column = logical_index
            self.sort_order = Qt.AscendingOrder
        self.table.sortItems(self.sort_column, self.sort_order)

    def update_process_list(self, system_info: dict):
        processes = system_info.get('processes', [])
        if not processes:
            return
            
        # Обновляем заголовок с количеством процессов
        self.setWindowTitle(f"Диспетчер задач - {len(processes)} процессов")
            
        # Сохраняем текущие процессы и их порядок
        current_processes = {}
        for row in range(self.table.rowCount()):
            try:
                item = self.table.item(row, 0)
                if item:
                    pid = item.data(Qt.UserRole + 1)
                    if pid:
                        current_processes[pid] = row
            except Exception as e:
                debug_print(f"Error reading current process: {e}")
                continue
            
        # Создаем список процессов для отображения
        process_list = []
        new_processes = []
        
        # Системные процессы и их PID
        system_processes = {
            "System": 4,
            "Registry": 8,
            "smss.exe": None,  # Session Manager Subsystem
            "csrss.exe": None,  # Client Server Runtime Subsystem
            "wininit.exe": None,  # Windows Initialization Process
            "services.exe": None,  # Services Control Manager
            "svchost.exe": None,  # Service Host
            "lsass.exe": None,  # Local Security Authority Subsystem Service
            "winlogon.exe": None,  # Windows Logon Process
            "explorer.exe": None  # Windows Explorer
        }
        
        for proc_info in processes:
            try:
                if not isinstance(proc_info, dict):
                    continue
                    
                name = proc_info.get('name', '')
                pid = proc_info.get('pid', 0)
                
                if not name or not pid:
                    continue
                    
                # Убираем цифры из скобок в имени процесса
                display_name = name
                
                # Проверяем, является ли процесс системным
                is_system = name in system_processes or pid == 0 or pid == 4
                
                cpu = proc_info.get('cpu_percent', 0.0)
                memory = proc_info.get('memory_info', {}).get('rss', 0) / (1024*1024)
                
                # Получаем данные о диске и сети напрямую - суммируем для более заметных значений
                disk_read = proc_info.get('disk_read', 0.0)
                disk_write = proc_info.get('disk_write', 0.0)
                disk_total = disk_read + disk_write  # Уже в МБ/с
                
                net_sent = proc_info.get('network_sent', 0.0)
                net_recv = proc_info.get('network_recv', 0.0)
                net_total = net_sent + net_recv  # Уже в МБ/с
                
                process_data = {
                    'pid': pid,
                    'name': display_name,
                    'cpu': cpu,
                    'memory': memory,
                    'disk': disk_total,
                    'network': net_total,
                    'is_system': is_system
                }
                
                # Если процесс уже был в списке, сохраняем его позицию
                if pid in current_processes:
                    process_data['position'] = current_processes[pid]
                    process_list.append(process_data)
                else:
                    # Новые процессы добавляем в отдельный список
                    process_data['position'] = 9999  # Высокое значение для сортировки в конец
                    new_processes.append(process_data)
            except Exception as e:
                debug_print(f"Error processing process info: {e}")
                continue
                
        # Сортируем существующие процессы по их текущим позициям
        process_list.sort(key=lambda x: x['position'])
        
        # Добавляем новые процессы в конец
        process_list.extend(new_processes)
        
        # Применяем текущую сортировку, если она есть
        def get_key_func(col):
            if col == 0:
                return lambda x: x['name']  # Сортировка по имени
            elif col == 1:
                return lambda x: x['cpu']   # Сортировка по CPU
            elif col == 2:
                return lambda x: x['memory']  # Сортировка по памяти
            elif col == 3:
                return lambda x: x['disk']    # Сортировка по диску
            elif col == 4:
                return lambda x: x['network']  # Сортировка по сети
            else:
                return lambda x: x['position']  # Сортировка по позиции
        
        key_func = get_key_func(self.sort_column)
        reverse = self.sort_order == Qt.DescendingOrder
        process_list.sort(key=key_func, reverse=reverse)
        
        # Обновляем таблицу, сохраняя порядок
        if self.table.rowCount() < len(process_list):
            self.table.setRowCount(len(process_list))
            
        for row, proc in enumerate(process_list):
            try:
                items = [
                    (0, f"{proc['name']}", proc['name']),
                    (1, f"{proc['cpu']:.1f}%", proc['cpu']),
                    (2, f"{proc['memory']:.1f} МБ", proc['memory']),
                    (3, f"{proc['disk']:.3f} МБ/с", proc['disk']),
                    (4, f"{proc['network']:.3f} МБ/с", proc['network'])
                ]
                
                for col, text, value in items:
                    item = self.table.item(row, col)
                    if item is None:
                        item = NumericTableWidgetItem(text)
                        self.table.setItem(row, col, item)
                    elif item.text() != text:
                        item.setText(text)
                    item.setData(Qt.UserRole, value)
                    
                    # Сохраняем PID в первой колонке для последующего определения позиции
                    if col == 0:
                        item.setData(Qt.UserRole + 1, proc['pid'])
                        
                        # Задаем цвет для системных процессов
                        if proc.get('is_system', False):
                            # Используем разные цвета для разных тем
                            color = QColor("#2d89ef" if self.is_dark_theme else "#0078d7")
                            item.setForeground(color)
                        else:
                            # Сбрасываем цвет для обычных процессов
                            item.setForeground(QColor("#ffffff" if self.is_dark_theme else "#000000"))
            except Exception as e:
                debug_print(f"Error updating table row {row}: {e}")
                continue

    def kill_selected_process(self):
        selected_items = self.table.selectedItems()
        if selected_items:
            row = selected_items[0].row()
            # Берем PID из дополнительных данных, которые мы сохраняем в Qt.UserRole + 1
            # вместо попытки извлечь его из текста
            item = self.table.item(row, 0)
            pid = item.data(Qt.UserRole + 1)
            if pid:
                try:
                    handle = ctypes.windll.kernel32.OpenProcess(
                        PROCESS_TERMINATE, False, pid
                    )
                    if handle:
                        ctypes.windll.kernel32.TerminateProcess(handle, -1)
                        ctypes.windll.kernel32.CloseHandle(handle)
                except Exception as e:
                    debug_print(f"Ошибка при попытке завершить процесс {pid}: {e}")

    def update_data(self, system_info: dict):
        # Обновляем все вкладки с новыми данными
        self.performance_tab.update_data(system_info)
        self.users_tab.update_data(system_info)
        self.update_process_list(system_info)


if __name__ == '__main__':
    # Проверяем запущено ли приложение с правами администратора
    def is_admin():
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except:
            return False
    
    # Если не запущено с правами администратора, перезапускаем с запросом прав
    if not is_admin():
        debug_print("Перезапуск с правами администратора для доступа ко всем процессам...")
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, 1
        )
        sys.exit(0)
    
    # Импортируем и применяем CPU оптимизацию
    try:
        from cpu_optimizer import optimize_process_priority, set_process_affinity
        optimize_process_priority()
        set_process_affinity()
    except ImportError:
        debug_print("CPU оптимизатор не найден, запуск без оптимизации")
    
    debug_print("Запуск диспетчера задач с правами администратора")
    app = QApplication(sys.argv)
    window = TaskManagerWindow()
    window.show()
    sys.exit(app.exec_())