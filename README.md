# Task Manager Documentation

## Обзор проекта

Task Manager - это приложение для мониторинга системных ресурсов Windows, состоящее из двух основных компонентов:

1. C++ DLL для сбора системной информации
2. Python GUI для отображения данных

## Структура проекта

### C++ компонент (Dll2)

#### ProcessInfo.h

```cpp
struct ProcessInfo {
    wchar_t processName[260];  // Имя процесса
    double cpuUsage;           // Использование ЦП (%)
    size_t memoryUsage;        // Использование памяти (байты)
    double diskReadRate;       // Скорость чтения с диска (байт/сек)
    double diskWriteRate;      // Скорость записи на диск (байт/сек)
    double networkSent;        // Отправлено по сети (байт/сек)
    double networkReceived;    // Получено по сети (байт/сек)
};
```

#### ProcessMonitor.h

```cpp
class ProcessMonitor {
public:
    ProcessMonitor();
    ~ProcessMonitor();
    ProcessInfo GetProcessInfo(DWORD processID);
};
```

**Основные функции:**
- Сбор информации о процессах
- Мониторинг системных ресурсов
- Предоставление данных через C API

### Python GUI (python_view)

#### Основные файлы

- **task_manager.py** - основной файл приложения
- **task_manager.spec** - конфигурация PyInstaller для основного приложения
- **launcher.spec** - конфигурация PyInstaller для лаунчера
- **start_app.bat** - скрипт для запуска приложения
- **requirements.txt** - зависимости Python

#### Основные классы

##### SystemMetrics

```python
class SystemMetrics:
    def __init__(self):
        self._setup_performance_counters()
        self._prev_cpu_times = self._get_cpu_times()
        self._prev_disk_counters = self._get_disk_counters()
        self._prev_net_counters = self._get_network_counters()
```

**Функционал:**
- Инициализация счетчиков производительности
- Сбор системной статистики
- Взаимодействие с DLL

##### TaskManagerWindow

```python
class TaskManagerWindow(QMainWindow):
    def __init__(self):
        self.collector = DataCollector()
        self.init_ui()
```

**Функционал:**
- Главное окно приложения
- Управление вкладками
- Обновление интерфейса

##### PerformanceTab

```python
class PerformanceTab(QWidget):
    def __init__(self, parent=None):
        self.init_data()
        self.init_ui()
```

**Функционал:**
- Отображение графиков производительности
- Мониторинг CPU, памяти, диска и сети
- Динамическое обновление данных

## Запуск приложения

### Через batch-файл

```
start_app.bat
```

Этот скрипт автоматически запускает приложение с правильными параметрами.

### Через Python

```
python task_manager.py
```

## Сборка

### DLL

```
# Visual Studio 2022
msbuild Dll2.sln /p:Configuration=Release /p:Platform=x64
```

### Python GUI

```
# Сборка основного приложения
pyinstaller --clean --onefile task_manager.spec

# Сборка лаунчера
pyinstaller --clean --onefile launcher.spec
```

## Технические требования и реализация

### 1. Независимость от среды запуска

**Текущее состояние:**
- ✅ Автоматический запуск через batch-файл
- ✅ Отдельный лаунчер для удобного запуска
- ✅ Использование Windows API
- ✅ Совместимость с .NET Framework

### 2. Stand-alone приложение

**Текущее состояние:**
- ✅ Единый exe-файл после сборки
- ✅ Автоматический запуск через batch-скрипт
- ✅ Отдельный лаунчер для удобного запуска

### 3. Оптимизация ресурсов

**Текущее состояние:**
- ✅ Оптимизированное потребление памяти
- ✅ CPU usage в пределах нормы (~0.7%)
- ✅ Эффективное использование системных ресурсов

## Целевые показатели

### 1. Ресурсы:
- CPU: <3% при нормальной работе
- Память: <60MB в пике
- Размер exe: <40MB

### 2. Зависимости:
- Только встроенные компоненты Windows
- .NET Framework (предустановлен)
- Visual C++ Runtime (статически слинкован)

### 3. Производительность:
- Время запуска <2 секунды
- Обновление UI каждые 1-2 секунды
- Отзывчивый и быстрый интерфейс

## Мониторинг соответствия требованиям

### 1. Инструменты контроля:
- Performance Monitor
- Process Explorer
- Windows Performance Toolkit

### 2. Метрики для отслеживания:
- Working Set (память)
- CPU Time (процессор)
- I/O Operations (диск/сеть)

### 3. Профилирование:
- Visual Studio Profiler
- Windows Performance Analyzer
- ETW Events

### ссылка на гитвики https://github.com/KJrTT/pain_task.git
