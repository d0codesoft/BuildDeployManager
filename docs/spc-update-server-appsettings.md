# appsettings.json для SPC.Update.Server

Файл `projects/examples/spc-update-server/appsettings.json` подготовлен по текущим
Program.cs, StorageOptions, IdentitySeeder, ReleaseSecurity,
ClientCertificateAuthenticationHandler и Database DependencyInjection проекта
`/path/to/example-update-server`. Значения реальных секретов не переносились.
Это production-шаблон: до запуска необходимо настроить подключение к БД; для
приёма/проверки официальных релизов — доверенные открытые ключи производителя.

## Размещение на Ubuntu

При обычной публикации ASP.NET Core загружает `appsettings.json` из content root
и затем `appsettings.Production.json`, если окружение Production. Подготовленная
служба использует Production и рабочий каталог
`/opt/spc-update-server/current/server`. Произвольный JSON в `/etc` автоматически
не загружается. Имя `appsettings.json` ниже относится к исходному шаблону;
при установке его лучше назвать `appsettings.Production.json`.

Не заменяйте файлы непосредственно внутри уже установленного проверяемого релиза:
это нарушит его manifest и проверку rollback. Для Build & Deploy Manager храните
production JSON вне релизов и подключайте его через существующий механизм shared.

После создания application root/shared администратором:

```bash
sudo install -o root -g spc-update-server -m 0640 projects/examples/spc-update-server/appsettings.json /opt/spc-update-server/shared/appsettings.Production.json
sudoedit /opt/spc-update-server/shared/appsettings.Production.json
```

Эта команда записывает файл: для существующего production JSON сначала сохраните
резервную копию и внесите изменения через sudoedit, не перезаписывайте его шаблоном.
Пользователь и группа spc-update-server должны быть созданы по инструкции службы.
В существующий раздел deploy YAML проекта добавьте:

```yaml
shared:
  server/appsettings.Production.json: appsettings.Production.json
```

При следующем deployment менеджер проверит исходный артефакт, затем добавит ссылку
на постоянный JSON. В опубликованном приложении не должно быть одноимённого
appsettings.Production.json: сейчас shared отказывается заменять существующий
файл. В проверенном локальном серверном проекте такого файла нет, но проверяйте
также пользовательские publish targets. Для изменения уже подключённого shared
JSON нужен перезапуск приложения, если конкретный параметр не поддерживает
динамическую перезагрузку; для единообразия используйте systemctl restart.

## Параметры

| Параметр | Назначение |
| --- | --- |
| Authentication:Admin:UserName | Имя первоначального администратора. Пустое значение вместе с пустым паролем отключает bootstrap. |
| Authentication:Admin:Password | Начальный пароль. Передавайте через Authentication__Admin__Password в защищённом env-файле. Созданному ранее пользователю пароль не заменяется. |
| Authentication:Admin:ProvisionPermissions | Для существующего пользователя добавляет недостающие permissions только при true и правильном указанном пароле. Новый bootstrap-пользователь получает все permissions независимо от этого флага. После настройки выключите и уберите bootstrap-пароль из конфигурации. |
| Authentication:ReadOnlyClientCertificates | Массив явно разрешённых клиентских certificate.Thumbprint. Требуется также успешная проверка цепочки доверия/отзыва и пригодности сертификата для client authentication. Даёт только Release.Read, не административный доступ. Пустой массив не разрешает ни одного сертификата. |
| Storage:WorkingDirectory | Временные файлы обработки загружаемых пакетов Update Server. Это не workspace сборок scdeploy. Абсолютный путь не зависит от версии приложения. Каталог создаётся приложением при наличии прав. |
| Storage:ReleaseDataDirectory | Постоянное хранилище распространяемых пакетов обновлений. Это не каталог версий самого веб-сервера /opt/.../releases. Его нужно сохранять и резервировать отдельно. |
| Database:Provider | Провайдер БД: Postgre, SQLServer или SQLite. Выбран Postgre; соответствующий ключ ConnectionStrings должен совпадать с нормализованным именем провайдера. |
| Database:EnsureCreated | Оставить false. При true текущий Program.cs намеренно завершает запуск с ошибкой — этот способ создания схемы больше не поддерживается. |
| Database:ApplyMigrations | Если true, применяет EF migrations на старте. В шаблоне false, чтобы не менять production-схему автоматически. Миграции выполняйте отдельно после проверки и резервной копии; проект поддерживает явный --migrate-only. |
| ConnectionStrings:Postgre | Подключение к PostgreSQL. Пустое значение требует реальной строки из ConnectionStrings__Postgre в env-файле. Это не рабочая строка подключения сама по себе. |
| ReleaseTrust:Keys | Словарь signingKeyId → полный открытый RSA PEM ключ производителя. Значение — содержимое ключа, а не путь к файлу. Текущий verifier требует RSA не менее 3072 бит и RSA-PSS-SHA256. Пустой словарь блокирует операции, требующие доверенного манифеста. Приватный ключ сюда не помещать. |
| ReleaseTrust:RevokedKeyIds | Массив отозванных signingKeyId. Ключ из этого списка отвергается даже при наличии в Keys. Сравнение идентификаторов в проверенном коде чувствительно к регистру. |
| ReleaseTrust:MsiSignerSha256 | Массив SHA-256 хешей сертификатов доверенных Authenticode-подписантов MSI. Это не хеш файла MSI и не клиентский certificate.Thumbprint. |
| Logging:LogLevel:Default | Общая детализация логов. Information для обычной эксплуатации, Debug временно для диагностики. |
| Logging:LogLevel:Microsoft.AspNetCore | Уровень сообщений ASP.NET Core. Warning уменьшает объём служебных сообщений. |
| Logging:LogLevel:Microsoft.EntityFrameworkCore.Database.Command | Уровень журналирования SQL-команд EF. Warning уменьшает SQL-шум; настройка не заменяет контроль секретов/чувствительных данных в логах приложения. |
| AllowedHosts | Разрешённые HTTP Host через точку с запятой, без схемы и пути. Домен updates.example.com и localhost/127.0.0.1 для локальной проверки /health. Замените/добавьте реальные имена. Это host filtering, а не CORS, firewall или список доверенных клиентов. |

## Секреты и приоритет настроек

Ранее подготовленная служба читает `/etc/scdeploy/apps/spc-update-server.env`.
Systemd читает файл до переключения на пользователя приложения, поэтому можно
использовать root:root и 0600. Пример формы строки (заменить значения локально):

```text
Database__Provider=Postgre
ConnectionStrings__Postgre="Host=127.0.0.1;Port=5432;Database=YOUR_DATABASE;Username=YOUR_USER;Password=YOUR_NEW_PASSWORD"
```

Для первого администратора при необходимости временно добавьте
Authentication__Admin__UserName и Authentication__Admin__Password. Не оставляйте
нового пользователя с демонстрационным паролем. После успешного создания
администратора уберите bootstrap-значения из env и JSON, затем перезапустите службу.
Наличие учётной записи в БД от этого не изменяется.

Переменные окружения перекрывают JSON; `__` соответствует `:` в имени параметра.
Не оставляйте строку с REPLACE_PASSWORD в env-примере: она перекроет даже правильно
заполненный JSON. Ранее подготовленный unit также задаёт Storage__WorkingDirectory,
Storage__ReleaseDataDirectory и оба флага Database. Изменения этих значений только
в JSON не повлияют на службу. Для переопределения задайте нужные значения в env-файле
либо пересмотрите соответствующие Environment в unit. Изменение unit требует
daemon-reload; изменение env требует перезапуска службы.

Порт 5151, production-окружение и forwarding задаются unit-файлом, поэтому Urls,
сертификаты Kestrel и параметры proxy не продублированы в JSON.

Открытые ключи производителя и подписанты должны соответствовать реальным
пакетам; новые случайные ключи не сделают существующие пакеты доверенными.
Ключи RSA в ReleaseTrust относятся к SPC Update Server и не являются Ed25519
ключами артефактов самого scdeploy.

В текущем MsiPublisherTrust проверка/публикация MSI требует Windows и на Linux
выбрасывает PlatformNotSupportedException. Заполнение MsiSignerSha256 не устраняет
это ограничение. Для публикации MSI на Ubuntu потребуется отдельное изменение
архитектуры проверки; данная конфигурация его не реализует.

Client certificate authentication требует, чтобы сертификат действительно дошёл
до Kestrel. Обычное HTTPS termination на Nginx не делает этого автоматически:
нужен отдельный проверенный certificate forwarding либо прямой Kestrel TLS.

## Проверка

```bash
python3 -m json.tool /opt/spc-update-server/shared/appsettings.Production.json >/dev/null
sudo systemctl restart spc-update-server.service
systemctl status spc-update-server.service --no-pager
curl -i http://127.0.0.1:5151/health
journalctl -u spc-update-server.service --no-pager -n 100
```

Текущий /health сообщает status=ok, но отдельно не проверяет работоспособность
БД и её схемы. Конфигурация проверена по исходникам и синтаксису JSON; подключение
к вашей production-БД и запуск на Ubuntu здесь не выполнялись.
