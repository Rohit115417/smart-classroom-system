from __future__ import annotations

import json
import os
import time
import uuid
from functools import wraps
from typing import Any

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://root:password@localhost:3306/smart_classroom",
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 280,
}

CORS(app, supports_credentials=True)
db = SQLAlchemy(app)

ROOM_TTL_SECONDS = 25
LOW_ATTENTION_THRESHOLD = 45
LOW_ATTENTION_STREAK_LIMIT = 3
CHAT_LIMIT = 150
ANNOUNCEMENT_LIMIT = 50
HISTORY_LIMIT = 30

# Real-time/WebRTC signaling is intentionally kept in memory because it is temporary.
analytics: dict[str, dict[str, dict[str, Any]]] = {}
signal_state: dict[str, dict[str, Any]] = {}
blocked_students_cache: dict[str, dict[str, dict[str, Any]]] = {}


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.Enum("teacher", "student"), nullable=False, index=True)
    student_code = db.Column(db.String(60), unique=True, nullable=True, index=True)
    created_at = db.Column(db.Integer, nullable=False)

    rooms = db.relationship("Room", backref="teacher", lazy=True)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "student_code": self.student_code,
        }


class Room(db.Model):
    __tablename__ = "rooms"

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(12), unique=True, nullable=False, index=True)
    room_name = db.Column(db.String(160), nullable=False)
    teacher_name = db.Column(db.String(120), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    subject = db.Column(db.String(120), default="")
    description = db.Column(db.Text, default="")
    password_hash = db.Column(db.String(255), nullable=True)
    is_live = db.Column(db.Boolean, default=False, nullable=False)
    teacher_camera_on = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.Integer, nullable=False)
    ended_at = db.Column(db.Integer, nullable=True)


class Attendance(db.Model):
    __tablename__ = "attendance"
    __table_args__ = (db.UniqueConstraint("room_id", "student_id", name="uq_attendance_room_student"),)

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(12), db.ForeignKey("rooms.room_id"), nullable=False, index=True)
    student_id = db.Column(db.String(60), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    name = db.Column(db.String(120), nullable=False)
    first_join_ts = db.Column(db.Integer, nullable=False)
    last_join_ts = db.Column(db.Integer, nullable=False)
    last_leave_ts = db.Column(db.Integer, nullable=True)
    total_watch_seconds = db.Column(db.Integer, default=0, nullable=False)
    is_present = db.Column(db.Boolean, default=False, nullable=False)
    join_count = db.Column(db.Integer, default=0, nullable=False)


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(16), unique=True, nullable=False)
    room_id = db.Column(db.String(12), db.ForeignKey("rooms.room_id"), nullable=False, index=True)
    ts = db.Column(db.Integer, nullable=False, index=True)
    author_type = db.Column(db.String(20), nullable=False)
    author_id = db.Column(db.String(60), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    text = db.Column(db.String(500), nullable=False)


class Announcement(db.Model):
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(16), unique=True, nullable=False)
    room_id = db.Column(db.String(12), db.ForeignKey("rooms.room_id"), nullable=False, index=True)
    ts = db.Column(db.Integer, nullable=False, index=True)
    teacher_name = db.Column(db.String(120), nullable=False)
    text = db.Column(db.String(700), nullable=False)


class BlockedStudent(db.Model):
    __tablename__ = "blocked_students"
    __table_args__ = (db.UniqueConstraint("room_id", "student_id", name="uq_blocked_room_student"),)

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(12), db.ForeignKey("rooms.room_id"), nullable=False, index=True)
    student_id = db.Column(db.String(60), nullable=False, index=True)
    reason = db.Column(db.String(255), nullable=False)
    removed_at = db.Column(db.Integer, nullable=False)


class RoomReport(db.Model):
    __tablename__ = "room_reports"

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(12), unique=True, nullable=False, index=True)
    created_at = db.Column(db.Integer, nullable=False)
    report_json = db.Column(db.Text, nullable=False)


class Assignment(db.Model):
    __tablename__ = "assignments"

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(12), db.ForeignKey("rooms.room_id"), nullable=False, index=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False)
    due_date = db.Column(db.String(40), nullable=True)
    created_at = db.Column(db.Integer, nullable=False)


class Submission(db.Model):
    __tablename__ = "submissions"
    __table_args__ = (db.UniqueConstraint("assignment_id", "student_id", name="uq_submission_assignment_student"),)

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignments.id"), nullable=False, index=True)
    student_id = db.Column(db.String(60), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    student_name = db.Column(db.String(120), nullable=False)
    content = db.Column(db.Text, nullable=False)
    status = db.Column(db.Enum("submitted", "reviewed"), default="submitted", nullable=False)
    submitted_at = db.Column(db.Integer, nullable=False)


def now_ts() -> int:
    return int(time.time())


def current_user() -> User | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, int(user_id))


def current_user_payload() -> dict[str, Any] | None:
    user = current_user()
    return user.to_public_dict() if user else None


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please login first.", "error")
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)

    return wrapper


def role_required(role: str):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                flash("Please login first.", "error")
                return redirect(url_for("login_page"))
            if user.role != role:
                flash("You do not have permission to open that page.", "error")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)

        return wrapper

    return decorator


def api_role_required(role: str):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return jsonify({"error": "Authentication required"}), 401
            if user.role != role:
                return jsonify({"error": "Permission denied"}), 403
            return view(*args, **kwargs)

        return wrapper

    return decorator


def get_room(room_id: str) -> Room | None:
    if not room_id:
        return None
    return Room.query.filter_by(room_id=room_id).first()


def ensure_signal_room(room_id: str) -> dict[str, Any]:
    if room_id not in signal_state:
        signal_state[room_id] = {"teacher": [], "students": {}, "next_id": 1}
    return signal_state[room_id]


def enqueue_signal(room_id: str, target: str, payload: dict[str, Any], student_id: str | None = None) -> dict[str, Any]:
    state = ensure_signal_room(room_id)
    message = {
        "id": state["next_id"],
        "ts": now_ts(),
        "student_id": student_id,
        "payload": payload,
    }
    state["next_id"] += 1

    if target == "teacher":
        state["teacher"].append(message)
        state["teacher"] = state["teacher"][-500:]
    else:
        if not student_id:
            raise ValueError("student_id is required for student signal queue")
        state["students"].setdefault(student_id, []).append(message)
        state["students"][student_id] = state["students"][student_id][-200:]
    return message


def chat_to_dict(message: ChatMessage) -> dict[str, Any]:
    return {
        "id": message.public_id,
        "ts": message.ts,
        "author_type": message.author_type,
        "author_id": message.author_id,
        "name": message.name,
        "text": message.text,
    }


def announcement_to_dict(item: Announcement) -> dict[str, Any]:
    return {
        "id": item.public_id,
        "ts": item.ts,
        "teacher_name": item.teacher_name,
        "text": item.text,
    }


def assignment_to_dict(assignment: Assignment, include_submissions: bool = False, student_id: str | None = None) -> dict[str, Any]:
    submissions_query = Submission.query.filter_by(assignment_id=assignment.id)
    submission_count = submissions_query.count()
    payload = {
        "id": assignment.id,
        "room_id": assignment.room_id,
        "title": assignment.title,
        "description": assignment.description,
        "due_date": assignment.due_date,
        "created_at": assignment.created_at,
        "submission_count": submission_count,
    }
    if student_id:
        submission = submissions_query.filter_by(student_id=student_id).first()
        payload["my_submission"] = submission_to_dict(submission) if submission else None
    if include_submissions:
        payload["submissions"] = [submission_to_dict(item) for item in submissions_query.order_by(Submission.submitted_at.desc()).all()]
    return payload


def submission_to_dict(submission: Submission | None) -> dict[str, Any] | None:
    if not submission:
        return None
    return {
        "id": submission.id,
        "assignment_id": submission.assignment_id,
        "student_id": submission.student_id,
        "student_name": submission.student_name,
        "content": submission.content,
        "status": submission.status,
        "submitted_at": submission.submitted_at,
    }


def room_payload(room_id: str) -> dict[str, Any]:
    room = get_room(room_id)
    if not room:
        raise KeyError("Room not found")
    prune_room_analytics(room_id)
    return {
        "room_id": room.room_id,
        "room_name": room.room_name,
        "teacher_name": room.teacher_name,
        "subject": room.subject or "",
        "description": room.description or "",
        "requires_password": bool(room.password_hash),
        "is_live": bool(room.is_live),
        "teacher_camera_on": bool(room.teacher_camera_on),
        "created_at": room.created_at,
        "viewer_count": len(analytics.get(room_id, {})),
    }


def append_chat(room_id: str, author_type: str, author_id: str, name: str, text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("message text is required")
    message = ChatMessage(
        public_id=str(uuid.uuid4())[:10],
        room_id=room_id,
        ts=now_ts(),
        author_type=(author_type or "student")[:20],
        author_id=(author_id or "unknown")[:60],
        name=(name or "Unknown")[:120],
        text=text[:500],
    )
    db.session.add(message)
    db.session.commit()
    return chat_to_dict(message)


def append_announcement(room_id: str, teacher_name: str, text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("announcement text is required")
    item = Announcement(
        public_id=str(uuid.uuid4())[:10],
        room_id=room_id,
        ts=now_ts(),
        teacher_name=(teacher_name or "Teacher")[:120],
        text=text[:700],
    )
    db.session.add(item)
    db.session.commit()
    return announcement_to_dict(item)


def mark_attendance_join(room_id: str, student_id: str, name: str, user_id: int | None = None) -> Attendance:
    entry = Attendance.query.filter_by(room_id=room_id, student_id=student_id).first()
    current = now_ts()
    if not entry:
        entry = Attendance(
            room_id=room_id,
            student_id=student_id,
            user_id=user_id,
            name=name or f"Student {student_id}",
            first_join_ts=current,
            last_join_ts=current,
            last_leave_ts=None,
            total_watch_seconds=0,
            is_present=True,
            join_count=1,
        )
        db.session.add(entry)
    else:
        entry.name = name or entry.name or f"Student {student_id}"
        entry.user_id = user_id or entry.user_id
        entry.last_join_ts = current
        entry.is_present = True
        entry.join_count = int(entry.join_count or 0) + 1
    db.session.commit()
    return entry


def mark_attendance_leave(room_id: str, student_id: str) -> Attendance | None:
    entry = Attendance.query.filter_by(room_id=room_id, student_id=student_id).first()
    if not entry:
        return None
    current = now_ts()
    if entry.is_present and entry.last_join_ts:
        entry.total_watch_seconds = int(entry.total_watch_seconds or 0) + max(0, current - int(entry.last_join_ts))
    entry.is_present = False
    entry.last_leave_ts = current
    db.session.commit()
    return entry


def finalize_room_attendance(room_id: str) -> None:
    for entry in Attendance.query.filter_by(room_id=room_id, is_present=True).all():
        mark_attendance_leave(room_id, entry.student_id)


def is_student_blocked(room_id: str, student_id: str | None) -> bool:
    if not room_id or not student_id:
        return False
    if student_id in blocked_students_cache.get(room_id, {}):
        return True
    return BlockedStudent.query.filter_by(room_id=room_id, student_id=student_id).first() is not None


def get_block_reason(room_id: str, student_id: str) -> str:
    cached = blocked_students_cache.get(room_id, {}).get(student_id)
    if cached:
        return cached.get("reason", "Removed by teacher")
    record = BlockedStudent.query.filter_by(room_id=room_id, student_id=student_id).first()
    return record.reason if record else "Removed by teacher"


def prune_room_analytics(room_id: str) -> None:
    current = now_ts()
    student_map = analytics.get(room_id, {})
    stale_ids = [sid for sid, entry in student_map.items() if current - entry.get("last_seen_ts", current) > ROOM_TTL_SECONDS]
    for sid in stale_ids:
        mark_attendance_leave(room_id, sid)
        student_map.pop(sid, None)
        state = signal_state.get(room_id)
        if state:
            state.get("students", {}).pop(sid, None)


def room_session_seconds(room_id: str) -> int:
    room = get_room(room_id)
    if not room:
        return 0
    end_ts = room.ended_at or now_ts()
    return max(0, end_ts - int(room.created_at or end_ts))


def build_room_report(room_id: str) -> dict[str, Any]:
    room = get_room(room_id)
    if not room:
        raise KeyError("Room not found")

    prune_room_analytics(room_id)
    student_rows = list(analytics.get(room_id, {}).values())
    attendance_rows = Attendance.query.filter_by(room_id=room_id).order_by(Attendance.name.asc()).all()

    avg_attention = 0
    if student_rows:
        avg_attention = round(sum(int(s.get("attention", 0)) for s in student_rows) / len(student_rows))

    low_attention_students = []
    leaderboard = []
    for student in student_rows:
        row = {
            "student_id": student.get("student_id"),
            "name": student.get("name"),
            "attention": int(student.get("attention", 0)),
            "status": student.get("status"),
        }
        leaderboard.append(row)
        if int(student.get("attention", 0)) < LOW_ATTENTION_THRESHOLD:
            low_attention_students.append(row)

    leaderboard.sort(key=lambda row: (-row["attention"], (row.get("name") or "").lower()))
    low_attention_students.sort(key=lambda row: (row["attention"], (row.get("name") or "").lower()))

    attendance_summary = []
    session_seconds = max(room_session_seconds(room_id), 1)
    current = now_ts()
    for entry in attendance_rows:
        total_watch_seconds = int(entry.total_watch_seconds or 0)
        if entry.is_present and entry.last_join_ts:
            total_watch_seconds += max(0, current - int(entry.last_join_ts))
        attendance_summary.append(
            {
                "student_id": entry.student_id,
                "name": entry.name,
                "join_count": int(entry.join_count or 0),
                "first_join_ts": entry.first_join_ts,
                "last_join_ts": entry.last_join_ts,
                "last_leave_ts": entry.last_leave_ts,
                "is_present": bool(entry.is_present),
                "total_watch_seconds": total_watch_seconds,
                "attendance_percent": round((total_watch_seconds / session_seconds) * 100, 1),
            }
        )

    return {
        "room": room_payload(room_id),
        "session_seconds": room_session_seconds(room_id),
        "total_students_seen": len(attendance_rows),
        "active_students": len(student_rows),
        "average_attention": avg_attention,
        "low_attention_students": low_attention_students,
        "leaderboard": leaderboard[:5],
        "attendance": attendance_summary,
        "chat_count": ChatMessage.query.filter_by(room_id=room_id).count(),
        "announcement_count": Announcement.query.filter_by(room_id=room_id).count(),
        "assignment_count": Assignment.query.filter_by(room_id=room_id).count(),
    }


def remove_student_from_room(room_id: str, student_id: str, reason: str = "Removed by teacher") -> dict[str, Any]:
    room = get_room(room_id)
    if not room:
        raise KeyError("Room not found")
    if not student_id:
        raise ValueError("student_id is required")

    record = BlockedStudent.query.filter_by(room_id=room_id, student_id=student_id).first()
    if not record:
        record = BlockedStudent(room_id=room_id, student_id=student_id, reason=reason, removed_at=now_ts())
        db.session.add(record)
    else:
        record.reason = reason
        record.removed_at = now_ts()
    db.session.commit()

    blocked_students_cache.setdefault(room_id, {})[student_id] = {"reason": reason, "removed_at": record.removed_at}
    mark_attendance_leave(room_id, student_id)
    analytics.get(room_id, {}).pop(student_id, None)
    ensure_signal_room(room_id)["students"].setdefault(student_id, [])

    message = enqueue_signal(
        room_id,
        "student",
        {"type": "removed_by_teacher", "reason": reason, "student_id": student_id},
        student_id=student_id,
    )
    enqueue_signal(
        room_id,
        "teacher",
        {"type": "student_left", "student_id": student_id, "reason": reason},
        student_id=student_id,
    )
    return message


@app.route("/")
def home():
    if not current_user():
        return redirect(url_for("login_page"))
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    if user and user.role == "teacher":
        return redirect(url_for("teacher_page"))
    return redirect(url_for("student_page"))


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid email or password.", "error")
            return render_template("login.html")
        session.clear()
        session["user_id"] = user.id
        session["role"] = user.role
        flash("Login successful.", "success")
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register_page():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        role = request.form.get("role") or "student"
        student_code = (request.form.get("student_code") or "").strip() or None

        if role not in {"teacher", "student"}:
            flash("Choose a valid role.", "error")
            return render_template("register.html")
        if not name or not email or len(password) < 6:
            flash("Name, email and a password of at least 6 characters are required.", "error")
            return render_template("register.html")
        if User.query.filter_by(email=email).first():
            flash("This email is already registered.", "error")
            return render_template("register.html")
        if role == "student" and student_code and User.query.filter_by(student_code=student_code).first():
            flash("This student ID is already registered.", "error")
            return render_template("register.html")

        user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            role=role,
            student_code=student_code if role == "student" else None,
            created_at=now_ts(),
        )
        db.session.add(user)
        db.session.commit()
        flash("Account created. Please login.", "success")
        return redirect(url_for("login_page"))
    return render_template("register.html")


@app.route("/logout")
def logout_page():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login_page"))


@app.route("/student")
@role_required("student")
def student_page():
    return render_template("index.html", current_user_payload=current_user_payload())


@app.route("/teacher")
@role_required("teacher")
def teacher_page():
    return render_template("teacher.html", current_user_payload=current_user_payload())


@app.post("/api/stream/create")
@api_role_required("teacher")
def create_room():
    data = request.get_json(silent=True) or {}
    user = current_user()
    room_id = str(uuid.uuid4())[:8]
    raw_password = (data.get("password") or "").strip()

    room = Room(
        room_id=room_id,
        room_name=(data.get("room_name") or f"Room {room_id}")[:160],
        teacher_name=(data.get("teacher_name") or user.name or "Teacher")[:120],
        teacher_id=user.id if user else None,
        subject=(data.get("subject") or "")[:120],
        description=(data.get("description") or ""),
        password_hash=generate_password_hash(raw_password) if raw_password else None,
        is_live=bool(data.get("is_live", False)),
        teacher_camera_on=False,
        created_at=now_ts(),
        ended_at=None,
    )
    db.session.add(room)
    db.session.commit()

    analytics[room_id] = {}
    blocked_students_cache[room_id] = {}
    ensure_signal_room(room_id)
    return jsonify({"status": "success", **room_payload(room_id)})


@app.get("/api/stream/rooms/active")
def active_rooms():
    payload = [room_payload(room.room_id) for room in Room.query.filter(Room.ended_at.is_(None)).all()]
    payload.sort(key=lambda item: (not item["is_live"], -item["created_at"]))
    return jsonify({"rooms": payload})


@app.get("/api/stream/room/<room_id>/info")
def room_info(room_id: str):
    if not get_room(room_id):
        return jsonify({"error": "Room not found"}), 404
    return jsonify(room_payload(room_id))


@app.post("/api/stream/room/<room_id>/status")
@api_role_required("teacher")
def room_status(room_id: str):
    room = get_room(room_id)
    if not room:
        return jsonify({"error": "Room not found"}), 404
    data = request.get_json(silent=True) or {}
    if "is_live" in data:
        room.is_live = bool(data["is_live"])
    if "teacher_camera_on" in data:
        room.teacher_camera_on = bool(data["teacher_camera_on"])
    db.session.commit()
    return jsonify({"status": "updated", **room_payload(room_id)})


@app.delete("/api/stream/room/<room_id>")
@api_role_required("teacher")
def delete_room(room_id: str):
    room = get_room(room_id)
    if not room:
        return jsonify({"error": "Room not found"}), 404

    finalize_room_attendance(room_id)
    room.ended_at = now_ts()
    room.is_live = False
    room.teacher_camera_on = False
    report = build_room_report(room_id)

    existing_report = RoomReport.query.filter_by(room_id=room_id).first()
    if not existing_report:
        existing_report = RoomReport(room_id=room_id, created_at=now_ts(), report_json=json.dumps(report))
        db.session.add(existing_report)
    else:
        existing_report.created_at = now_ts()
        existing_report.report_json = json.dumps(report)
    db.session.commit()

    analytics.pop(room_id, None)
    signal_state.pop(room_id, None)
    blocked_students_cache.pop(room_id, None)
    return jsonify({"status": "deleted", "room_id": room_id, "report": report})


@app.post("/api/student/join")
@api_role_required("student")
def student_join():
    data = request.get_json(silent=True) or {}
    user = current_user()
    room_id = data.get("room_id")
    student_id = (data.get("student_id") or user.student_code or str(user.id)).strip()
    supplied_password = (data.get("password") or "").strip()

    room = get_room(room_id)
    if not room:
        return jsonify({"error": "Room not found"}), 404
    if not student_id:
        return jsonify({"error": "student_id is required"}), 400
    if is_student_blocked(room_id, student_id):
        return jsonify({"error": get_block_reason(room_id, student_id), "status": "removed"}), 403
    if room.password_hash and not check_password_hash(room.password_hash, supplied_password):
        return jsonify({"error": "Invalid room password"}), 403

    student_name = data.get("name") or user.name or f"Student {student_id}"
    analytics.setdefault(room_id, {})[student_id] = {
        "student_id": student_id,
        "name": student_name,
        "attention": 0,
        "mood": "😐 Neutral",
        "status": "joining",
        "last_seen_ts": now_ts(),
        "last_seen": now_ts(),
        "joined_at": now_ts(),
        "hand_raised": False,
        "low_attention_streak": 0,
        "alert_active": False,
        "alert_count": 0,
        "last_alert_ts": None,
        "attention_history": [],
        "tab_switch_count": 0,
        "away_count": 0,
    }

    mark_attendance_join(room_id, student_id, student_name, user_id=user.id)
    ensure_signal_room(room_id)["students"].setdefault(student_id, [])
    enqueue_signal(room_id, "teacher", {"type": "student_joined", "student_id": student_id, "name": student_name}, student_id=student_id)
    return jsonify({"status": "joined", **room_payload(room_id)})


@app.post("/api/student/update")
@api_role_required("student")
def student_update():
    data = request.get_json(silent=True) or {}
    room_id = data.get("watch_signals", {}).get("room_id")
    user = current_user()
    student_id = (data.get("student_id") or user.student_code or str(user.id)).strip()

    if not get_room(room_id):
        return jsonify({"error": "Room not found"}), 404
    if not student_id:
        return jsonify({"error": "student_id is required"}), 400
    if is_student_blocked(room_id, student_id):
        return jsonify({"error": get_block_reason(room_id, student_id), "status": "removed"}), 403

    existing = analytics.setdefault(room_id, {}).get(student_id, {})
    attention_score = max(0, min(100, int(data.get("attention_score", 0))))
    current_status = data.get("status") or "active"
    history = list(existing.get("attention_history", []))
    history.append({"ts": now_ts(), "attention": attention_score})
    history = history[-HISTORY_LIMIT:]

    low_attention_streak = int(existing.get("low_attention_streak", 0))
    if attention_score < LOW_ATTENTION_THRESHOLD:
        low_attention_streak += 1
    else:
        low_attention_streak = 0

    alert_active = bool(existing.get("alert_active", False))
    alert_count = int(existing.get("alert_count", 0))
    last_alert_ts = existing.get("last_alert_ts")

    analytics[room_id][student_id] = {
        "student_id": student_id,
        "name": data.get("name") or existing.get("name") or user.name or f"Student {student_id}",
        "attention": attention_score,
        "mood": data.get("emotion") or "😐 Neutral",
        "status": current_status,
        "last_seen_ts": now_ts(),
        "last_seen": now_ts(),
        "joined_at": existing.get("joined_at") or now_ts(),
        "hand_raised": bool(data.get("hand_raised", existing.get("hand_raised", False))),
        "low_attention_streak": low_attention_streak,
        "alert_active": alert_active,
        "alert_count": alert_count,
        "last_alert_ts": last_alert_ts,
        "attention_history": history,
        "tab_switch_count": int(data.get("tab_switch_count", existing.get("tab_switch_count", 0))),
        "away_count": int(data.get("away_count", existing.get("away_count", 0))),
    }

    if low_attention_streak >= LOW_ATTENTION_STREAK_LIMIT and not alert_active:
        analytics[room_id][student_id]["alert_active"] = True
        analytics[room_id][student_id]["alert_count"] = alert_count + 1
        analytics[room_id][student_id]["last_alert_ts"] = now_ts()
        enqueue_signal(
            room_id,
            "teacher",
            {
                "type": "low_attention_alert",
                "student_id": student_id,
                "name": analytics[room_id][student_id]["name"],
                "attention": attention_score,
                "message": f"{analytics[room_id][student_id]['name']} has low attention.",
            },
            student_id=student_id,
        )
    elif low_attention_streak == 0:
        analytics[room_id][student_id]["alert_active"] = False

    prune_room_analytics(room_id)
    return jsonify({"status": "received", "viewer_count": len(analytics.get(room_id, {}))})


@app.post("/api/student/leave")
@api_role_required("student")
def student_leave():
    data = request.get_json(silent=True) or {}
    user = current_user()
    room_id = data.get("room_id")
    student_id = data.get("student_id") or user.student_code or str(user.id)

    if room_id in analytics and student_id in analytics[room_id]:
        mark_attendance_leave(room_id, student_id)
        analytics[room_id].pop(student_id, None)

    if room_id in signal_state and student_id:
        signal_state[room_id].get("students", {}).pop(student_id, None)
        enqueue_signal(room_id, "teacher", {"type": "student_left", "student_id": student_id}, student_id=student_id)
    return jsonify({"status": "left"})


@app.post("/api/stream/room/<room_id>/student/<student_id>/remove")
@api_role_required("teacher")
def teacher_remove_student(room_id: str, student_id: str):
    if not get_room(room_id):
        return jsonify({"error": "Room not found"}), 404

    known_student = (
        student_id in ensure_signal_room(room_id).get("students", {})
        or student_id in analytics.get(room_id, {})
        or is_student_blocked(room_id, student_id)
        or Attendance.query.filter_by(room_id=room_id, student_id=student_id).first() is not None
    )
    if not known_student:
        return jsonify({"error": "Student not found"}), 404

    data = request.get_json(silent=True) or {}
    reason = data.get("reason") or "Removed by teacher"
    remove_student_from_room(room_id, student_id, reason=reason)
    return jsonify({"status": "removed", "room_id": room_id, "student_id": student_id, "reason": reason})


@app.post("/api/stream/room/<room_id>/student/<student_id>/hand")
@api_role_required("student")
def update_hand_state(room_id: str, student_id: str):
    if not get_room(room_id):
        return jsonify({"error": "Room not found"}), 404
    if student_id not in analytics.get(room_id, {}):
        return jsonify({"error": "Student not found"}), 404

    data = request.get_json(silent=True) or {}
    hand_raised = bool(data.get("hand_raised", False))
    analytics[room_id][student_id]["hand_raised"] = hand_raised
    enqueue_signal(
        room_id,
        "teacher",
        {
            "type": "hand_update",
            "student_id": student_id,
            "name": analytics[room_id][student_id].get("name"),
            "hand_raised": hand_raised,
        },
        student_id=student_id,
    )
    return jsonify({"status": "updated", "hand_raised": hand_raised})


@app.get("/api/stream/room/<room_id>/analytics")
@api_role_required("teacher")
def room_analytics(room_id: str):
    if not get_room(room_id):
        return jsonify({"error": "Room not found"}), 404

    prune_room_analytics(room_id)
    students = list(analytics.get(room_id, {}).values())
    students.sort(key=lambda row: row.get("name", "").lower())
    return jsonify({"room": room_payload(room_id), "students": students, "viewer_count": len(students), "report": build_room_report(room_id)})


@app.get("/api/stream/room/<room_id>/report")
@api_role_required("teacher")
def room_report(room_id: str):
    if get_room(room_id):
        return jsonify(build_room_report(room_id))
    saved = RoomReport.query.filter_by(room_id=room_id).first()
    if saved:
        return jsonify(json.loads(saved.report_json))
    return jsonify({"error": "Room not found"}), 404


@app.get("/api/stream/room/<room_id>/chat")
def get_room_chat(room_id: str):
    if not get_room(room_id):
        return jsonify({"error": "Room not found"}), 404
    since_ts = int(request.args.get("since", 0))
    query = ChatMessage.query.filter(ChatMessage.room_id == room_id, ChatMessage.ts > since_ts).order_by(ChatMessage.ts.asc(), ChatMessage.id.asc())
    messages = [chat_to_dict(message) for message in query.limit(CHAT_LIMIT).all()]
    return jsonify({"messages": messages})


@app.post("/api/stream/room/<room_id>/chat")
def post_room_chat(room_id: str):
    if not get_room(room_id):
        return jsonify({"error": "Room not found"}), 404
    data = request.get_json(silent=True) or {}
    try:
        message = append_chat(
            room_id,
            author_type=(data.get("author_type") or "student")[:20],
            author_id=(data.get("author_id") or "unknown")[:60],
            name=(data.get("name") or "Unknown")[:80],
            text=data.get("text") or "",
        )
        return jsonify({"status": "sent", "message": message})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/stream/room/<room_id>/announcements")
def get_announcements(room_id: str):
    if not get_room(room_id):
        return jsonify({"error": "Room not found"}), 404
    since_ts = int(request.args.get("since", 0))
    query = Announcement.query.filter(Announcement.room_id == room_id, Announcement.ts > since_ts).order_by(Announcement.ts.asc(), Announcement.id.asc())
    items = [announcement_to_dict(item) for item in query.limit(ANNOUNCEMENT_LIMIT).all()]
    return jsonify({"announcements": items})


@app.post("/api/stream/room/<room_id>/announcements")
@api_role_required("teacher")
def post_announcements(room_id: str):
    room = get_room(room_id)
    if not room:
        return jsonify({"error": "Room not found"}), 404
    data = request.get_json(silent=True) or {}
    try:
        item = append_announcement(room_id, room.teacher_name, data.get("text") or "")
        for sid in list(ensure_signal_room(room_id).get("students", {}).keys()):
            enqueue_signal(room_id, "student", {"type": "announcement", "announcement": item}, student_id=sid)
        return jsonify({"status": "sent", "announcement": item})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/stream/sfu/student/<room_id>/events")
@api_role_required("student")
def student_events(room_id: str):
    if not get_room(room_id):
        return jsonify({"error": "Room not found"}), 404

    student_id = request.args.get("student_id", "unknown")

    def event_stream():
        while True:
            if not get_room(room_id):
                yield f"data: {json.dumps({'type': 'closed', 'message': 'Room closed'})}\n\n"
                break
            if is_student_blocked(room_id, student_id):
                yield f"data: {json.dumps({'type': 'removed_by_teacher', 'message': get_block_reason(room_id, student_id)})}\n\n"
                break
            payload = {"type": "info", "student_id": student_id, **room_payload(room_id), "server_time": now_ts()}
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(5)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/api/stream/room/<room_id>/signal/teacher")
@api_role_required("student")
def signal_to_teacher(room_id: str):
    if not get_room(room_id):
        return jsonify({"error": "Room not found"}), 404
    data = request.get_json(silent=True) or {}
    student_id = data.get("student_id")
    payload = data.get("payload")
    if not student_id or not payload:
        return jsonify({"error": "student_id and payload are required"}), 400
    if is_student_blocked(room_id, student_id):
        return jsonify({"error": get_block_reason(room_id, student_id), "status": "removed"}), 403
    message = enqueue_signal(room_id, "teacher", payload, student_id=student_id)
    return jsonify({"status": "queued", "message_id": message["id"]})


@app.get("/api/stream/room/<room_id>/signal/teacher")
@api_role_required("teacher")
def get_teacher_signals(room_id: str):
    if not get_room(room_id):
        return jsonify({"error": "Room not found"}), 404
    since = int(request.args.get("since", 0))
    messages = [m for m in ensure_signal_room(room_id)["teacher"] if m["id"] > since]
    return jsonify({"messages": messages})


@app.post("/api/stream/room/<room_id>/signal/student/<student_id>")
@api_role_required("teacher")
def signal_to_student(room_id: str, student_id: str):
    if not get_room(room_id):
        return jsonify({"error": "Room not found"}), 404
    data = request.get_json(silent=True) or {}
    payload = data.get("payload")
    if not payload:
        return jsonify({"error": "payload is required"}), 400
    message = enqueue_signal(room_id, "student", payload, student_id=student_id)
    return jsonify({"status": "queued", "message_id": message["id"]})


@app.get("/api/stream/room/<room_id>/signal/student/<student_id>")
@api_role_required("student")
def get_student_signals(room_id: str, student_id: str):
    if not get_room(room_id):
        return jsonify({"error": "Room not found"}), 404
    since = int(request.args.get("since", 0))
    queue = ensure_signal_room(room_id)["students"].setdefault(student_id, [])
    if is_student_blocked(room_id, student_id):
        already_has_remove = any((m.get("payload") or {}).get("type") == "removed_by_teacher" for m in queue)
        if not already_has_remove:
            enqueue_signal(
                room_id,
                "student",
                {"type": "removed_by_teacher", "reason": get_block_reason(room_id, student_id), "student_id": student_id},
                student_id=student_id,
            )
            queue = ensure_signal_room(room_id)["students"].setdefault(student_id, [])
    messages = [m for m in queue if m["id"] > since]
    return jsonify({"messages": messages})


@app.get("/api/assignments")
@login_required
def list_assignments():
    user = current_user()
    room_id = request.args.get("room_id")
    query = Assignment.query
    if room_id:
        query = query.filter_by(room_id=room_id)
    if user.role == "teacher":
        query = query.filter_by(teacher_id=user.id)
        items = [assignment_to_dict(item, include_submissions=True) for item in query.order_by(Assignment.created_at.desc()).all()]
    else:
        student_id = user.student_code or str(user.id)
        items = [assignment_to_dict(item, student_id=student_id) for item in query.order_by(Assignment.created_at.desc()).all()]
    return jsonify({"assignments": items})


@app.post("/api/assignments")
@api_role_required("teacher")
def create_assignment():
    user = current_user()
    data = request.get_json(silent=True) or {}
    room_id = (data.get("room_id") or "").strip()
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    due_date = (data.get("due_date") or "").strip() or None

    if not get_room(room_id):
        return jsonify({"error": "Create/select a valid room first"}), 400
    if not title or not description:
        return jsonify({"error": "title and description are required"}), 400

    item = Assignment(
        room_id=room_id,
        teacher_id=user.id,
        title=title[:180],
        description=description,
        due_date=due_date[:40] if due_date else None,
        created_at=now_ts(),
    )
    db.session.add(item)
    db.session.commit()
    return jsonify({"status": "created", "assignment": assignment_to_dict(item, include_submissions=True)})


@app.post("/api/assignments/<int:assignment_id>/submit")
@api_role_required("student")
def submit_assignment(assignment_id: int):
    user = current_user()
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "submission content is required"}), 400
    assignment = db.session.get(Assignment, assignment_id)
    if not assignment:
        return jsonify({"error": "Assignment not found"}), 404

    student_id = user.student_code or str(user.id)
    submission = Submission.query.filter_by(assignment_id=assignment_id, student_id=student_id).first()
    if not submission:
        submission = Submission(
            assignment_id=assignment_id,
            student_id=student_id,
            user_id=user.id,
            student_name=user.name,
            content=content,
            status="submitted",
            submitted_at=now_ts(),
        )
        db.session.add(submission)
    else:
        submission.content = content
        submission.status = "submitted"
        submission.submitted_at = now_ts()
    db.session.commit()
    return jsonify({"status": "submitted", "submission": submission_to_dict(submission)})


@app.get("/api/assignments/<int:assignment_id>/submissions")
@api_role_required("teacher")
def assignment_submissions(assignment_id: int):
    assignment = db.session.get(Assignment, assignment_id)
    if not assignment:
        return jsonify({"error": "Assignment not found"}), 404
    submissions = Submission.query.filter_by(assignment_id=assignment_id).order_by(Submission.submitted_at.desc()).all()
    return jsonify({"submissions": [submission_to_dict(item) for item in submissions]})


@app.cli.command("init-db")
def init_db_command():
    """Create database tables."""
    db.create_all()
    print("Database tables created.")


def load_blocked_cache() -> None:
    blocked_students_cache.clear()
    for item in BlockedStudent.query.all():
        blocked_students_cache.setdefault(item.room_id, {})[item.student_id] = {"reason": item.reason, "removed_at": item.removed_at}


with app.app_context():
    if os.getenv("AUTO_CREATE_TABLES", "true").lower() in {"1", "true", "yes"}:
        db.create_all()
    load_blocked_cache()


if __name__ == "__main__":
    print("Server is launching on http://127.0.0.1:5000 ...")
    app.run(host="127.0.0.1", port=5000, debug=True, threaded=True)
