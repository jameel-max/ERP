# دليل النشر الإنتاجي — نظام رويال فروت

> النظام جاهز للتشغيل الفعلي. اتبع الخطوات التالية بالترتيب. كل الأوامر تُنفَّذ من جذر المشروع.

---

## 1) المتطلبات

- Python 3.12 أو أحدث.
- (اختياري) PostgreSQL 14+ — النظام يعمل افتراضياً على SQLite.
- (مستحسن) وكيل عكسي `nginx` مع شهادة HTTPS.

---

## 2) التثبيت

```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 3) الإعدادات (ملف .env)

```powershell
Copy-Item deploy\env.example .env
```

ثم افتح `.env` وعدّل:

1. **`SECRET_KEY`** — ولّده بأمر:
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(64))"
   ```
2. **`DJANGO_DEBUG=False`** (إلزامي في الإنتاج).
3. **`DJANGO_ALLOWED_HOSTS`** — ضع النطاق الحقيقي، مثال: `erp.example.com`.
4. **`DJANGO_CSRF_TRUSTED_ORIGINS`** — مثال: `https://erp.example.com`.
5. (اختياري) فعّل أسطر PostgreSQL بإزالة التعليق عنها وضبط بياناتها.

> ملف `.env` يُقرأ تلقائياً عند تشغيل Django ولا يحتاج أي مكتبة إضافية. لا تشاركه علناً.

---

## 4) تهيئة قاعدة البيانات والمستخدم الأول

```powershell
python manage.py migrate
python manage.py init_system
```

`init_system` يُنشئ (مرة واحدة، وهو آمن للتكرار):
- **حسابا إدارة فقط**: `ammar / ammar123` و `salah / salah123` — كلاهما مالك نظام بصلاحيات كاملة متطابقة (كل العمليات والموافقات والتفريغ).
- إعدادات الشركة. **بدون أي بيانات تجريبية.**
- إن وُجدت حسابات أخرى فسيتم حذفها ليبقى النظام بحسابَي الإدارة.

> **مهم:** سجّل الدخول فوراً وغيّر كلمتي المرور من لوحة الإدارة `/admin/` أو من صفحة المستخدم.

---

## 4-ب) استيراد البيانات القديمة (اختياري)

- من الواجهة: صفحة **استيراد CSV** على المسار `/import-csv/` (التجار والصناديق).

---

## 4-ج) بيانات النظام بعد النشر

- أدخل القيم الفعلية من صفحة **إعدادات النظام**: أوزان الصناديق/الطبالي، أجور العمال، حد فرق الوزن، سعات البرادات والأسياخ، مدة تنبيه FIFO، أسعار التوضيب وخصم الصناديق.
- راجع قسم «السياسات التي تحتاج اعتماد صاحب المنشأة» في `DELIVERY_REPORT_AR.md`.

---

## 5) التشغيل

### Windows

```powershell
.\scripts\serve_production.ps1
```

### Linux / macOS

```bash
chmod +x scripts/serve_production.sh
./scripts/serve_production.sh
```

السكربت يطبّق الهجرات، ويجمّع الملفات الساكنة، ويشغّل `check --deploy`، ثم يشغّل خادم **Waitress** على المنفذ المحدد (افتراضي `8000`).

---

## 6) الملفات الساكنة

- تُقدَّم تلقائياً عبر **WhiteNoise** (مضمّن في `requirements.txt`).
- الخطوط والمكتبات الأمامية (Bootstrap, Bootstrap Icons, Chart.js, Cairo) **محلية داخل `static/vendor`**، لذا يعمل النظام دون إنترنت.
- لتحديثها من مصادرها: `python scripts/fetch_frontend_assets.py`.

---

## 7) التشغيل كخدمة (Linux — systemd)

```bash
sudo cp deploy/royal-fruit.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now royal-fruit
sudo systemctl status royal-fruit
```

> عدّل المسارات والمستخدم داخل ملف الخدمة حسب بيئتك.

---

## 8) الوكيل العكسي و HTTPS (nginx)

انسخ `deploy/nginx.conf.example` وعدّل النطاق والمسارات، ثم:

```bash
sudo certbot --nginx -d erp.example.com
```

---

## 9) النسخ الاحتياطي

```powershell
python manage.py backup_db
```

يُنشئ نسخة من قاعدة البيانات في مجلد `backups/`. يُنصح بجدولته يومياً (Windows Task Scheduler أو cron).

مثال cron يومي الساعة 2 صباحاً:

```
0 2 * * * cd /opt/royal-fruit-erp && .venv/bin/python manage.py backup_db >> logs/backup.log 2>&1
```

---

## 10) قائمة تحقق قبل التشغيل الفعلي

- [ ] `DJANGO_DEBUG=False`
- [ ] `SECRET_KEY` فريد وطويل
- [ ] `DJANGO_ALLOWED_HOSTS` و `DJANGO_CSRF_TRUSTED_ORIGINS` مضبوطان
- [ ] تم تغيير كلمات مرور حسابَي الإدارة (ammar و salah) الافتراضية
- [ ] `python manage.py check --deploy` بلا تحذيرات حرجة (مع `DJANGO_DEBUG=False` و`SECRET_KEY` طويل ومختلف)
- [ ] `python manage.py collectstatic --noinput` نُفِّذ بنجاح
- [ ] النسخ الاحتياطي مجدول ويعمل
- [ ] HTTPS مُفعّل
