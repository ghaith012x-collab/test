import os, sys, json, threading, time
from flask import Flask, render_template, jsonify, request, Response
from bot import (
    start_worker, stop_worker, get_screenshot, get_worker_status,
    workers, screenshots, last_frame_ts, browser_sessions, create_placeholder
)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)

# === ROUTES ===
@app.route("/")
def index():
    return render_template("site.html")

@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json() or {}
    username = data.get("username", "test_user")
    email = data.get("email", "zeroghaith2012@gmail.com")
    password = data.get("password", "")
    dob = data.get("dob", "1995-01-01")
    tor_offset = data.get("tor_offset", 0)
    
    if not password:
        return jsonify({"error": "Password required"}), 400
    
    success = start_worker(username, email, password, dob, tor_offset)
    return jsonify({"started": success, "username": username})

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
    return jsonify({
        "status": status,
        "has_screenshot": ss is not None,
        "last_frame_age": round(time.time() - last, 1),
        "browser_alive": username in browser_sessions
    })

@app.route("/live/<username>")
def live_feed(username):
    """Live cam endpoint - returns current screenshot with no-cache headers."""
    img = screenshots.get(username)
    if not img or time.time() - last_frame_ts.get(username, 0) > 10:
        img = create_placeholder(username, "Waiting for signal...")
    
    buf = getattr(img, "_buf", None)
    if buf is None:
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
