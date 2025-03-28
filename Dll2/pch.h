// pch.h: это предварительно скомпилированный заголовочный файл.
// Перечисленные ниже файлы компилируются только один раз, что ускоряет последующие сборки.
// Это также влияет на работу IntelliSense, включая многие функции просмотра и завершения кода.
// Однако изменение любого из приведенных здесь файлов между операциями сборки приведет к повторной компиляции всех(!) этих файлов.
// Не добавляйте сюда файлы, которые планируете часто изменять, так как в этом случае выигрыша в производительности не будет.

#ifndef PCH_H
#define PCH_H

// Исключите редко используемые компоненты из заголовков Windows
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX

// Windows headers
#include <windows.h>
#include <psapi.h>
#include <pdh.h>
#include <stdint.h>

// STL headers
#include <vector>
#include <memory>
#include <string>
#include <unordered_map>
#include <chrono>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <queue>
#include <functional>

// Project headers - must come after all system headers
#include "framework.h"

#endif //PCH_H