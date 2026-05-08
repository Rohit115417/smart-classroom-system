# AI-Powered Smart Classroom & Assignment Management System

A Flask + MySQL full-stack web application for live classroom management, student attention tracking, attendance, chat, announcements, WebRTC live media, and assignment/task submissions.

## Features

### Authentication and roles
- Teacher registration/login
- Student registration/login
- Session-based authentication
- Role-based route and API protection

### Teacher
- Create password-protected live rooms
- Start/pause/end live class
- Camera/mic and screen-share support through browser WebRTC
- Live student analytics
- Low-attention alerts
- Attendance report and CSV export
- Announcements and live chat
- Assignment/task creation
- View student submissions

### Student
- Join live room using Room ID and password
- Watch teacher live media
- Focus/attention tracking using MediaPipe Face Mesh
- Raise hand
- Chat and announcements
- View assignments
- Submit or update task answers

## Tech stack

- Frontend: HTML, CSS, JavaScript
- Backend: Flask
- Database: MySQL
- ORM: Flask-SQLAlchemy
- Auth: Flask sessions + Werkzeug password hashing
- Realtime media: WebRTC signaling through Flask endpoints

## Folder structure

```text
smart_classroom_mysql/
├── server.py
├── requirements.txt
├── .env.example
├── README.md
└── templates/
    ├── login.html
    ├── register.html
    ├── index.html
    └── teacher.html
```

## Setup steps

### 1. Create MySQL database

Open MySQL and run:

```sql
CREATE DATABASE smart_classroom CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 2. Create virtual environment

```bash
python -m venv venv
```

Activate it:

```bash
# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

Copy `.env.example` to `.env`:

```bash
copy .env.example .env
```

On macOS/Linux:

```bash
cp .env.example .env
```

Edit `.env` and change your MySQL password:

```env
DATABASE_URL=mysql+pymysql://root:your_mysql_password@localhost:3306/smart_classroom
```

### 5. Start the Flask app

```bash
python server.py
```

Open:

```text
http://127.0.0.1:5000
```

## Demo flow

1. Register a teacher account.
2. Login as teacher.
3. Create a room.
4. Copy the Room ID.
5. Start camera/mic and click **Go Live**.
6. Register a student account in another browser/incognito window.
7. Login as student.
8. Enter the Room ID and room password.
9. Join the class.
10. Teacher can create assignments.
11. Student can submit assignments.
12. Teacher can view submissions and export attendance.

## Important notes

- Browser camera, mic, and screen-share require HTTPS in production. Localhost works in modern browsers.
- WebRTC signaling is stored in memory because it is temporary by nature.
- MySQL stores users, rooms, attendance, chat messages, announcements, assignments, submissions, blocked students, and reports.

## Suggested GitHub description

AI-powered live classroom system with attention tracking, attendance analytics, chat, announcements, teacher-student roles, and assignment management using Flask and MySQL.
