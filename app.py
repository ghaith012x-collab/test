import os, sys, json, threading, time
from flask import Flask, render_template, jsonify, request, Response
from bot import (
    start_worker, stop_worker, get_screenshot, get_worker_status,
    workers, screenshots, last_frame_ts, browser_sessions, create_placeholder,
    generate_password, generate_dob,
)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)

# === ROUTES ===
@app.route("/")
def index():
    return render_template("site.html")

@app.route("/healthz")
@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json() or {}
    username = data.get("username", "test_user").strip() or "test_user"
    email = data.get("email", "zeroghaith2012@gmail.com").strip()
    auto_password = bool(data.get("auto_password", True))
    auto_dob = bool(data.get("auto_dob", True))
    password = data.get("password", "") if not auto_password else ""
    dob = data.get("dob", "") if not auto_dob else ""

    if not email:
        return jsonify({"error": "Email required"}), 400
    if not auto_password and not password:
        return jsonify({"error": "Password required"}), 400

    # Pre-generate for display so the user can save the account.
    gen_password = generate_password() if (auto_password or not password) else password
    gen_dob = generate_dob() if (auto_dob or not dob) else dob

    success = start_worker(
        username, email, gen_password, gen_dob,
        data.get("tor_offset", 0), auto_password, auto_dob
    )
    return jsonify({
        "started": success,
        "username": username,
        "generated": {"password": gen_password, "dob": gen_dob} if (auto_password or auto_dob) else None
    })

@app.route("/api/stop", methods=["POST"])
def api_stop():
    data = request.get_json() or {}
    username = data.get("username", "test_user")
    stop_worker(username)
    return jsonify({"stopped": True, "username": username})

@app.route("/api/status/<username>")
def api_status(username):
    status = get_worker_status(username)
    ss = screenshots.get(username)
    last = last_frame_ts.get(username, 0)
    log_text = ""
    try:
        from database import get_log
        log_text = get_log(username)
    except Exception:
        pass
    return jsonify({
        "status": status,
        "has_screenshot": ss is not None,
        "last_frame_age": round(time.time() - last, 1),
        "browser_alive": username in browser_sessions,
        "log": log_text
    })

@app.route("/live/<username>")
def live_feed(username):
    """Live cam endpoint - returns current screenshot with no-cache headers."""
    img = screenshots.get(username)
    if not img or time.time() - last_frame_ts.get(username, 0) > 10:
        img = create_placeholder(username, "Waiting for signal...")

    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return Response(buf.getvalue(), mimetype="image/png", headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0"
    })

@app.route("/api/otp", methods=["POST"])
def api_otp():
    """Manual OTP injection endpoint."""
    data = request.get_json() or {}
    username = data.get("username", "test_user")
    otp = data.get("otp", "")
    
    # Store OTP for worker to pick up
    if username in workers:
        workers[username]["manual_otp"] = otp
        return jsonify({"received": True})
    return jsonify({"error": "No active worker"}), 404

@app.route("/api/workers")
def api_workers():
    return jsonify({
        "workers": list(workers.keys()),
        "screenshots": list(screenshots.keys())
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
