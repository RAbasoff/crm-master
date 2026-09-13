# Деплой на PythonAnywhere — git
# ===============================

## На локальном компьютере (сначала!)
```bash
cd D:\ИИ\CRM_Мастерская
git add app.py models.py utils.py templates/users.html templates/change_password.html DEPLOY.md
git commit -m "update: passwords, permissions, login tracking"
git push origin main
```

## На PythonAnywhere (консоль Bash)
```bash
cd ~/crm-master
git pull origin master
```

## Потом
Web → кнопка Reload
