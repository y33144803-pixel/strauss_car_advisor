FROM python:3.12-slim

ENV DEV_RELOAD=0 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STRAUSS_DB_PATH=/data/advisor.db

WORKDIR /app

# התלויות קודם - כך שכבת ההתקנה נשמרת במטמון כשרק הקוד משתנה
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# מסד הנתונים נשמר בתיקייה חיצונית (/data) כדי שיישרוד הפעלה מחדש של הקונטיינר
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/_stcore/health' % os.environ.get('PORT','8501'), timeout=4)"

# הפלטפורמות בענן מעבירות את הפורט במשתנה PORT; מקומית ברירת המחדל 8501
CMD ["sh", "-c", "streamlit run frontend/app.py --server.address=0.0.0.0 --server.port=${PORT:-8501} --server.headless=true"]
