import hashlib
import secrets
from datetime import timedelta

from flask import abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename


def register_collab_routes(app, ctx):
    current_user = ctx["current_user"]
    login_required = ctx["login_required"]
    require_permission = ctx["require_permission"]
    is_note_manager = ctx["is_note_manager"]
    can_edit_note = ctx["can_edit_note"]
    get_db = ctx["get_db"]
    utc_now = ctx["utc_now"]
    to_iso = ctx["to_iso"]
    ATTACHMENTS_DIR = ctx["ATTACHMENTS_DIR"]
    mask_face_identifier = ctx["mask_face_identifier"]
    face_identifier_log_value = ctx["face_identifier_log_value"]
    html_from_md = ctx["html_from_md"]

    def json_payload_or_400():
        payload = request.get_json(silent=True)
        if payload is None:
            return None, (jsonify({"error": "Invalid JSON payload"}), 400)
        return payload, None

    @app.get("/notes")
    @login_required
    @require_permission("notes:read")
    def notes_index():
        user = current_user()
        if not user:
            abort(401)
        if is_note_manager(user):
            notes = get_db().execute(
                "SELECT n.id,n.title,n.note_type,n.updated_at,n.depot_scope,u.username owner FROM notes n JOIN users u ON u.id=n.owner_id ORDER BY n.updated_at DESC LIMIT 50"
            ).fetchall()
        else:
            notes = get_db().execute(
                "SELECT n.id,n.title,n.note_type,n.updated_at,n.depot_scope,u.username owner FROM notes n JOIN users u ON u.id=n.owner_id WHERE n.depot_scope=? ORDER BY n.updated_at DESC LIMIT 50",
                (user["depot_assignment"],),
            ).fetchall()
        return render_template("notes.html", notes=notes)

    @app.post("/api/notes")
    @login_required
    @require_permission("notes:write")
    def save_note():
        payload, error = json_payload_or_400()
        if error:
            return error
        note_id = payload.get("id")
        title = (payload.get("title") or "").strip()
        if not title:
            return jsonify({"error": "Title required"}), 422
        content_md = payload.get("content_md", "")
        note_type = payload.get("note_type", "training")
        if note_type not in {"training", "incident"}:
            return jsonify({"error": "Invalid note_type"}), 422

        db = get_db()
        user = current_user()
        if not user:
            return jsonify({"error": "Unauthorized"}), 401
        now = to_iso(utc_now())
        if note_id:
            note = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if not note:
                return jsonify({"error": "Note not found"}), 404
            if not can_edit_note(user, note):
                return jsonify({"error": "Forbidden"}), 403
            version_no = db.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM note_versions WHERE note_id=?", (note_id,)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO note_versions (note_id,version_no,title,content_md,created_by,created_at) VALUES (?,?,?,?,?,?)",
                (note_id, version_no, note["title"], note["content_md"], session["user_id"], now),
            )
            db.execute(
                "UPDATE notes SET title=?, content_md=?, updated_at=? WHERE id=?",
                (title, content_md, now, note_id),
            )
            db.execute(
                "DELETE FROM note_versions WHERE id IN (SELECT id FROM note_versions WHERE note_id=? ORDER BY version_no DESC LIMIT -1 OFFSET 20)",
                (note_id,),
            )
            db.commit()
            return jsonify({"ok": True, "id": note_id})

        cursor = db.execute(
            "INSERT INTO notes (title,content_md,note_type,owner_id,depot_scope,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (title, content_md, note_type, session["user_id"], user["depot_assignment"], now, now),
        )
        db.commit()
        return jsonify({"ok": True, "id": cursor.lastrowid})

    @app.post("/api/notes/<int:note_id>/attachments")
    @login_required
    @require_permission("notes:write")
    def upload_attachment(note_id):
        note = get_db().execute("SELECT owner_id,depot_scope FROM notes WHERE id=?", (note_id,)).fetchone()
        if not note:
            return jsonify({"error": "Note not found"}), 404
        if not can_edit_note(current_user(), note):
            return jsonify({"error": "Forbidden"}), 403
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "File required"}), 400
        if request.content_length and request.content_length > 20 * 1024 * 1024:
            return jsonify({"error": "File too large"}), 413
        filename = secure_filename(file.filename or "attachment.bin")
        stored = ATTACHMENTS_DIR / f"{note_id}_{secrets.token_hex(4)}_{filename}"
        file.save(stored)
        size = stored.stat().st_size
        if size > 20 * 1024 * 1024:
            stored.unlink(missing_ok=True)
            return jsonify({"error": "File too large"}), 413
        get_db().execute(
            "INSERT INTO note_attachments (note_id,filename,stored_path,size_bytes,uploaded_by,uploaded_at) VALUES (?,?,?,?,?,?)",
            (note_id, filename, str(stored), size, session["user_id"], to_iso(utc_now())),
        )
        get_db().commit()
        return jsonify({"ok": True})

    @app.post("/api/notes/link")
    @login_required
    @require_permission("notes:write")
    def link_notes():
        payload, error = json_payload_or_400()
        if error:
            return error
        try:
            left = int(payload["from_note_id"])
            right = int(payload["to_note_id"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Invalid from_note_id/to_note_id"}), 422
        link_type = payload.get("link_type", "related")
        db = get_db()
        user = current_user()
        left_note = db.execute("SELECT owner_id,depot_scope,note_type FROM notes WHERE id=?", (left,)).fetchone()
        right_note = db.execute("SELECT owner_id,depot_scope,note_type FROM notes WHERE id=?", (right,)).fetchone()
        if not left_note or not right_note:
            return jsonify({"error": "Note not found"}), 404
        if not can_edit_note(user, left_note) or not can_edit_note(user, right_note):
            return jsonify({"error": "Forbidden"}), 403
        pair = {left_note["note_type"], right_note["note_type"]}
        if pair != {"incident", "training"}:
            return jsonify({"error": "Links must be between one incident and one training note"}), 422
        db.execute(
            "INSERT OR IGNORE INTO note_links (from_note_id,to_note_id,link_type) VALUES (?,?,?)",
            (left, right, link_type),
        )
        db.execute(
            "INSERT OR IGNORE INTO note_links (from_note_id,to_note_id,link_type) VALUES (?,?,?)",
            (right, left, link_type),
        )
        db.commit()
        return jsonify({"ok": True})

    @app.get("/api/notes/<int:note_id>/versions")
    @login_required
    @require_permission("notes:write")
    def note_versions(note_id):
        db = get_db()
        note = db.execute("SELECT owner_id,depot_scope FROM notes WHERE id=?", (note_id,)).fetchone()
        if not note:
            return jsonify({"error": "Note not found"}), 404
        if not can_edit_note(current_user(), note):
            return jsonify({"error": "Forbidden"}), 403

        rows = db.execute(
            """
            SELECT nv.version_no, nv.title, nv.created_at, u.username AS created_by
            FROM note_versions nv
            JOIN users u ON u.id = nv.created_by
            WHERE nv.note_id=?
            ORDER BY nv.version_no DESC
            LIMIT 20
            """,
            (note_id,),
        ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.get("/api/notes/<int:note_id>/render")
    @login_required
    @require_permission("notes:read")
    def render_note_markdown(note_id):
        db = get_db()
        note = db.execute("SELECT owner_id,depot_scope,content_md,title FROM notes WHERE id=?", (note_id,)).fetchone()
        if not note:
            return jsonify({"error": "Note not found"}), 404
        viewer = current_user()
        if not viewer:
            return jsonify({"error": "Unauthorized"}), 401
        if not is_note_manager(viewer) and note["depot_scope"] != viewer["depot_assignment"]:
            return jsonify({"error": "Forbidden"}), 403

        return jsonify({
            "note_id": note_id,
            "title": note["title"],
            "html": html_from_md(note["content_md"]),
        })

    @app.post("/api/notes/<int:note_id>/rollback/<int:version_no>")
    @login_required
    @require_permission("notes:write")
    def rollback_note(note_id, version_no):
        db = get_db()
        note = db.execute("SELECT owner_id,depot_scope FROM notes WHERE id=?", (note_id,)).fetchone()
        if not note:
            return jsonify({"error": "Note not found"}), 404
        if not can_edit_note(current_user(), note):
            return jsonify({"error": "Forbidden"}), 403
        version = db.execute(
            "SELECT title,content_md FROM note_versions WHERE note_id=? AND version_no=?",
            (note_id, version_no),
        ).fetchone()
        if not version:
            return jsonify({"error": "Version not found"}), 404
        db.execute(
            "UPDATE notes SET title=?, content_md=?, updated_at=? WHERE id=?",
            (version["title"], version["content_md"], to_iso(utc_now()), note_id),
        )
        db.commit()
        return jsonify({"ok": True})

    @app.get("/api/notes/rollup")
    @login_required
    def notes_rollup():
        db = get_db()
        user = current_user()
        if not user:
            return jsonify([])
        if is_note_manager(user):
            data = db.execute(
                """
                SELECT n.note_type, COUNT(*) AS total,
                    SUM(CASE WHEN nl.id IS NOT NULL THEN 1 ELSE 0 END) AS linked
                FROM notes n
                LEFT JOIN note_links nl ON nl.from_note_id=n.id
                GROUP BY n.note_type
                """
            ).fetchall()
        else:
            data = db.execute(
                """
                SELECT n.note_type, COUNT(*) AS total,
                    SUM(CASE WHEN nl.id IS NOT NULL THEN 1 ELSE 0 END) AS linked
                FROM notes n
                LEFT JOIN note_links nl ON nl.from_note_id=n.id
                WHERE n.depot_scope=?
                GROUP BY n.note_type
                """,
                (user["depot_assignment"],),
            ).fetchall()
        return jsonify([dict(row) for row in data])

    @app.post("/api/social/action")
    @login_required
    @require_permission("social:use")
    def social_action():
        payload, error = json_payload_or_400()
        if error:
            return error
        try:
            target_id = int(payload["target_user_id"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Invalid target_user_id"}), 422
        relation = payload.get("relation")
        if relation is None:
            return jsonify({"error": "relation is required"}), 422
        if relation not in {"follow", "block", "report", "favorite", "like", "unfollow"}:
            return jsonify({"error": "Invalid relation"}), 422
        actor_id = session["user_id"]
        if target_id == actor_id:
            return jsonify({"error": "Cannot relate to self"}), 400

        db = get_db()
        target_user = db.execute("SELECT id FROM users WHERE id=?", (target_id,)).fetchone()
        if not target_user:
            return jsonify({"error": "target_user_id not found"}), 404
        if relation == "unfollow":
            db.execute(
                "DELETE FROM relationships WHERE user_a=? AND user_b=? AND relation='follow'",
                (actor_id, target_id),
            )
        else:
            db.execute(
                "INSERT OR IGNORE INTO relationships (user_a,user_b,relation,created_at) VALUES (?,?,?,?)",
                (actor_id, target_id, relation, to_iso(utc_now())),
            )
        db.commit()
        return jsonify({"ok": True})

    @app.get("/profiles/<int:user_id>")
    @login_required
    def profile(user_id):
        viewer = session["user_id"]
        db = get_db()
        owner = db.execute(
            "SELECT id,username,role,depot_assignment,face_identifier_encrypted FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
        if not owner:
            abort(404)
        blocked = db.execute(
            "SELECT 1 FROM relationships WHERE user_a=? AND user_b=? AND relation='block'",
            (owner["id"], viewer),
        ).fetchone()
        if blocked:
            abort(403)

        follow_a = db.execute(
            "SELECT 1 FROM relationships WHERE user_a=? AND user_b=? AND relation='follow'",
            (viewer, owner["id"]),
        ).fetchone()
        follow_b = db.execute(
            "SELECT 1 FROM relationships WHERE user_a=? AND user_b=? AND relation='follow'",
            (owner["id"], viewer),
        ).fetchone()
        mutual = bool(follow_a and follow_b)
        masked_face = ""
        if owner["face_identifier_encrypted"]:
            raw = app.fernet.decrypt(owner["face_identifier_encrypted"]).decode("utf-8")
            masked_face = mask_face_identifier(raw)
            app.logger.info(
                "profile_face_identifier_access owner_id=%s masked_face_identifier=%s",
                owner["id"],
                face_identifier_log_value(raw),
            )
        return render_template("profile.html", owner=owner, mutual=mutual, masked_face=masked_face)

    def assign_variant(user_id, experiment_id):
        hash_key = hashlib.sha256(f"{user_id}:{experiment_id}".encode("utf-8")).hexdigest()
        return "A" if int(hash_key[-1], 16) % 2 == 0 else "B"

    @app.get("/api/experiments/assign/<widget_key>")
    @login_required
    def experiment_assign(widget_key):
        db = get_db()
        exp = db.execute("SELECT * FROM experiments WHERE widget_key=?", (widget_key,)).fetchone()
        if not exp or not exp["enabled"]:
            return jsonify({"variant": "A", "label": "Version A", "enabled": False})
        assignment = db.execute(
            "SELECT variant FROM experiment_assignments WHERE experiment_id=? AND user_id=?",
            (exp["id"], session["user_id"]),
        ).fetchone()
        if not assignment:
            variant = assign_variant(session["user_id"], exp["id"])
            db.execute(
                "INSERT INTO experiment_assignments (experiment_id,user_id,variant,created_at) VALUES (?,?,?,?)",
                (exp["id"], session["user_id"], variant, to_iso(utc_now())),
            )
            db.commit()
        else:
            variant = assignment["variant"]
        label = exp["label_a"] if variant == "A" else exp["label_b"]
        return jsonify({"variant": variant, "label": label, "enabled": True})

    @app.get("/supervisor/experiments")
    @login_required
    @require_permission("experiments:manage")
    def supervisor_experiments():
        exps = get_db().execute("SELECT * FROM experiments ORDER BY widget_key").fetchall()
        return render_template("experiments.html", experiments=exps)

    @app.post("/supervisor/experiments/<int:exp_id>/toggle")
    @login_required
    @require_permission("experiments:manage")
    def toggle_experiment(exp_id):
        enabled = int(request.form.get("enabled", "1"))
        get_db().execute("UPDATE experiments SET enabled=? WHERE id=?", (enabled, exp_id))
        get_db().commit()
        return redirect(url_for("supervisor_experiments"))

    @app.get("/analyst/metrics")
    @login_required
    @require_permission("analytics:view")
    def analyst_metrics():
        db = get_db()
        impressions = db.execute(
            "SELECT COUNT(*) FROM analytics_events WHERE event_type='rec_impression'"
        ).fetchone()[0]
        clicks = db.execute("SELECT COUNT(*) FROM analytics_events WHERE event_type='rec_click'").fetchone()[0]
        bookings = db.execute(
            "SELECT COUNT(*) FROM analytics_events WHERE event_type='booking_confirmed'"
        ).fetchone()[0]

        seven_days_ago = to_iso(utc_now() - timedelta(days=7))
        returns = db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM analytics_events WHERE created_at >= ? GROUP BY user_id HAVING COUNT(*) > 1",
            (seven_days_ago,),
        ).fetchall()
        return_usage = len(returns)

        rank = db.execute(
            """
            SELECT
              CASE WHEN (tp + fp) > 0 THEN 1.0 * tp / (tp + fp) ELSE 0 END AS precision,
              CASE WHEN (tp + fn) > 0 THEN 1.0 * tp / (tp + fn) ELSE 0 END AS recall,
              avg_ndcg AS ndcg,
              avg_coverage AS coverage,
              avg_diversity AS diversity
            FROM (
              SELECT
                SUM(CASE WHEN recommended=1 AND relevant=1 THEN 1 ELSE 0 END) AS tp,
                SUM(CASE WHEN recommended=1 AND relevant=0 THEN 1 ELSE 0 END) AS fp,
                SUM(CASE WHEN recommended=0 AND relevant=1 THEN 1 ELSE 0 END) AS fn,
                AVG(ndcg) AS avg_ndcg,
                AVG(covered) AS avg_coverage,
                AVG(diverse) AS avg_diversity
              FROM ranking_samples
            )
            """
        ).fetchone()

        ctr = (clicks / impressions) if impressions else 0
        conversion = (bookings / clicks) if clicks else 0
        return render_template(
            "metrics.html",
            ctr=round(ctr, 3),
            conversion=round(conversion, 3),
            return_usage=return_usage,
            rank=rank,
        )
