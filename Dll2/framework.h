#pragma once

// Исключите редко используемые компоненты из заголовков Windows
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX

// Файлы заголовков Windows
#include <windows.h>
#include <stdint.h>

#ifdef DLL2_EXPORTS
#define DLL2_API __declspec(dllexport)
#else
#define DLL2_API __declspec(dllimport)
#endif