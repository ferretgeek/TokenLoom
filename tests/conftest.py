from __future__ import annotations

import base64
import os

from argon2 import PasswordHasher

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("ADMIN_KEY_HASH", PasswordHasher().hash("synthetic-test-admin-key"))
os.environ.setdefault("SESSION_SECRET", "synthetic-test-session-secret-with-more-than-32-bytes")
os.environ.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(bytes(range(32))).decode("ascii"))
os.environ.setdefault("ALLOWED_HOSTS", "localhost,127.0.0.1,::1,testserver")
