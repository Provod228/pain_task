#pragma once
#include "framework.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct _ProcessInfo {
    wchar_t processName[MAX_PATH];  // Имя процесса
    double cpuUsage;                // Использование ЦП в процентах
    size_t memoryUsage;             // Использование памяти в байтах
    double diskReadRate;            // Скорость чтения с диска (байт/сек)
    double diskWriteRate;           // Скорость записи на диск (байт/сек)
    double networkSent;             // Отправлено по сети (байт/сек)
    double networkReceived;         // Получено по сети (байт/сек)
} ProcessInfo;

#ifdef __cplusplus
}
#endif