#pragma once
#include "framework.h"
#include "ProcessInfo.h"

#ifdef __cplusplus
class ProcessMonitor {
public:
    ProcessMonitor();
    ~ProcessMonitor();
    ProcessInfo GetProcessInfo(DWORD processID);

private:
    ProcessMonitor(const ProcessMonitor&) = delete;
    ProcessMonitor& operator=(const ProcessMonitor&) = delete;
};
#endif

#ifdef __cplusplus
extern "C" {
#endif

// Экспортируемая функция
DLL2_API ProcessInfo __stdcall GetProcessInfo(DWORD processID);

#ifdef __cplusplus
}
#endif 