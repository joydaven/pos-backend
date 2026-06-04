FROM python:3.11

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DJANGO_DEBUG=True

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000", "--verbosity=3"]
