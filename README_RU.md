
# Электронное кредитное досье v0.3

Версия 0.3 добавляет:

- OCR для сканированных PDF;
- чтение фотографий;
- постраничное определение цифрового текста и OCR;
- новые типы документов:
  - акт приёма-передачи;
  - график платежей;
  - дополнительное соглашение;
  - квитанция о подписании;
- универсальное извлечение для неизвестных документов;
- поиск ИИН/БИН, VIN, IBAN и денежных сумм;
- отображение метода чтения и уровня уверенности.

## 1. Установите Python и зависимости

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 2. Установите Tesseract OCR

Нужен Tesseract 5 для Windows.

Во время установки включите языки:

```text
Russian
Kazakh
English
```

После установки проверьте:

```powershell
tesseract --version
tesseract --list-langs
```

В списке должны быть:

```text
rus
kaz
eng
```

Если команда `tesseract` не находится, добавьте папку установки в PATH. Обычно:

```text
C:\Program Files\Tesseract-OCR
```

Либо добавьте в начало `run.py`:

```python
import pytesseract
pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)
```

## 3. Запуск

```powershell
python run.py
```

Откройте:

```text
http://127.0.0.1:5000
```

## Поддерживаемые форматы

```text
PDF
PNG
JPG / JPEG
TIFF / TIF
BMP
WEBP
```

## Важное ограничение

Ни одна программа не может одинаково точно распознать абсолютно любой документ без обучения на его шаблоне.

Версия 0.3 действует по уровням:

1. известный тип — типовой парсер;
2. неизвестный тип — универсальные реквизиты;
3. скан или фото — OCR;
4. низкая уверенность — ручная проверка пользователем.

Для каждого нового типа документов нужно добавлять примеры и тесты.
