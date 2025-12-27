# Lecture abstract Generator

Веб-приложение для обработки видео лекций с Яндекс Диска и создания PDF-конспектов с преобразованием речи в текст и AI-реферированием.

## Архитектура

Приложение состоит из:

1. Веб-интерфейс — Простой HTML-интерфейс для отправки ссылок на лекции и просмотра статуса задач
2. Бэкенд на Python — Flask-приложение для обработки HTTP-запросов и управления задачами
3. Фоновый воркер — Асинхронная обработка видеофайлов
4. Yandex Database (YDB) — Хранение статуса задач и метаданных
5. Очередь сообщений — Управление асинхронной обработкой
6. Объектное хранилище — Хранение сгенерированных PDF-файлов
7. Yandex SpeechKit — Преобразование речи в текст
8. YandexGPT — Генерация конспектов лекций



## Использованные яндекс-сервисы

- Yandex API Gateway — HTTP-эндпоинт API
- Yandex Object Storage — Хранение PDF-файлов
- Yandex VPC — Сетевая инфраструктура
- Yandex Managed Service for YDB — База данных
- Yandex Message Queue — Асинхронная обработка
- Yandex Lockbox — Безопасное хранение секретов
- Yandex IAM — Управление доступом
- Yandex SpeechKit — Распознавание речи
- YandexGPT API — Реферирование текста

## Инструкция
1. Подготовка окружения
- Настройте токен Яндекс Облака:

   ```bash
   export YC_TOKEN="ваш-yandex-cloud-токен" 
   ```
- Получите ваш Cloud ID и Folder ID:
   
   ```bash
   yc resource manager cloud list
   yc resource manager folder list
   ```
2. Настройка Terraform
- Создайте файл terraform.tfvars в директории terraform/:

   ```bash
   cd terraform
   cp terraform.tfvars.example terraform.tfvars
   ```
- Отредактируйте terraform.tfvars вашими значениями:

```hcl
cloud_id  = "b1gxxxxxxxxxxxxxxxxxxx"
folder_id = "b1gxxxxxxxxxxxxxxxxxxx"
prefix    = "lecture-notes"  
yc_token = "..."
```
3. Развертывание инфраструктуры
- Разверните все ресурсы Яндекс Облака:

   ```bash
   cd terraform
   terraform init
   terraform plan
   terraform apply
   ```
- Будут созданы:
   - Сеть и группа безопасности
   - Бакет объектного хранилища
   - База данных YDB
   - Очередь сообщений
   - API Gateway
   - Service Account с необходимыми правами

4. Получение URL приложения
- URL API Gateway будет в выводе Terraform:
   ```bash
   terraform output api_gateway_url
   ```
Также вы можете просмотреть его в Яндекс Cloud Console в разделе API Gateway.

5. Откройте приложение в браузере по URL API Gateway
- Отправьте лекцию:
- Введите название лекции
- Укажите публичную ссылку на видео с Яндекс Диска
- Нажмите "Generate Lecture Notes"
- Скачайте PDF когда статус будет DONE