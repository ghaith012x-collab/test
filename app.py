import os, sys, json, threading, time
from flask import Flask, render_template, jsonify, request, Response
from bot import (
    start_worker, stop_worker, get_screenshot, get_worker_status,
    workers, screenshots, last_frame_ts, browser_sessions, create_placeholder,
    generate_password, generate_dob, generate_username,
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
    try:
        data = request.get_json() or {}
        username = "test_user"
        email = data.get("email", "zeroghaith2012@gmail.com").strip()
        auto_password = bool(data.get("auto_password", True))
        auto_dob = bool(data.get("auto_dob", True))
        password = data.get("password", "") if not auto_password else ""
        dob = data.get("dob", "") if not auto_dob else ""

        log.debug(f"api_start: username={username} email={email} auto_password={auto_password} auto_dob={auto_dob}")

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
        log.debug(f"api_start: start_worker returned {success}")
        return jsonify({
            "started": success,
            "username": username,
            "generated": {"password": gen_password, "dob": gen_dob} if (auto_password or auto_dob) else None
        })
    except Exception as e:
        log.exception(f"api_start error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/stop", methods=["POST"])
def api_stop():
    try:
        data = request.get_json() or {}
        username = data.get("username", "test_user")
        log.debug(f"api_stop: {username}")
        stop_worker(username)
        return jsonify({"stopped": True, "username": username})
    except Exception as e:
        log.exception(f"api_stop error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/status/<username>")
def api_status(username):
    try:
        status = get_worker_status(username)
        ss = screenshots.get(username)
        last = last_frame_ts.get(username, 0)
        age = round(time.time() - last, 1)
        log.debug(f"api_status {username}: browser_alive={username in browser_sessions} frame_age={age}")
        log_text = ""
        try:
            from database import get_log
            log_text = get_log(username)
        except Exception as e:
            log.debug(f"api_status log fetch error: {e}")
        return jsonify({
            "status": status,
            "has_screenshot": ss is not None,
            "last_frame_age": age,
            "browser_alive": username in browser_sessions,
            "log": log_text
        })
    except Exception as e:
        log.exception(f"api_status error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/live/<username>")
def live_feed(username):
    """Live cam endpoint - returns current screenshot with no-cache headers.

    Always serves the most recent captured frame (even if it is older than
    10s) so the cam never goes black just because the worker is idle or a
    single capture failed. Only falls back to a placeholder if we have never
    captured anything for this user.
    """
    log.debug(f"live_feed {username} requested")
    img = screenshots.get(username)
    if img is None:
        img = create_placeholder(username, "Waiting for signal...")
    try:
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return Response(buf.getvalue(), mimetype="image/png", headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0"
        })
    except Exception as e:
        log.error(f"live_feed render error: {e}")
        return ("", 500)

@app.route("/api/otp", methods=["POST"])
def api_otp():
    """Manual OTP injection endpoint."""
    try:
        data = request.get_json() or {}
        username = data.get("username", "test_user")
        otp = data.get("otp", "")
        log.debug(f"api_otp: username={username} otp_len={len(otp)}")

        # Store OTP for worker to pick up
        if username in workers:
            workers[username]["manual_otp"] = otp
            return jsonify({"received": True})
        log.warning(f"api_otp: no active worker for {username}")
        return jsonify({"error": "No active worker"}), 404
    except Exception as e:
        log.exception(f"api_otp error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/workers")
def api_workers():
    return jsonify({
        "workers": list(workers.keys()),
        "screenshots": list(screenshots.keys())
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
