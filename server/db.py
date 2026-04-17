"""
MongoDB connection singleton.

Uses PyMongo directly — no Django ORM.
Connection string and database name come from os.getenv().
"""

import os
from pymongo import MongoClient
from pymongo.errors import CollectionInvalid, ConnectionFailure

# Module-level cache for the MongoClient instance
_client = None
PROJECT_SETTINGS_COLLECTION = "project_settings"
CHAT_SESSIONS_COLLECTION = "chat_sessions"


def get_client():
    """Return a cached MongoClient instance (created on first call)."""
    global _client
    if _client is None:
        uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    return _client


def get_db():
    """Return the application database."""
    db_name = os.getenv("MONGODB_NAME", "product_discovery")
    return get_client()[db_name]


def get_collection(name):
    """Shorthand to get a collection by name from the app database."""
    return get_db()[name]


def ensure_indexes():
    """Create required collection and indexes (idempotent on repeated calls)."""
    db = get_db()

    if PROJECT_SETTINGS_COLLECTION not in db.list_collection_names():
        try:
            db.create_collection(PROJECT_SETTINGS_COLLECTION)
        except CollectionInvalid:
            # Another process may have created it between the check and create.
            pass

    col = db[PROJECT_SETTINGS_COLLECTION]
    col.create_index("project_name", unique=True)

    if CHAT_SESSIONS_COLLECTION not in db.list_collection_names():
        try:
            db.create_collection(CHAT_SESSIONS_COLLECTION)
        except CollectionInvalid:
            pass

    chat_col = db[CHAT_SESSIONS_COLLECTION]
    chat_col.create_index("project_id")


# Create indexes when the module is first imported
try:
    ensure_indexes()
except ConnectionFailure:
    # MongoDB may not be reachable at import time (e.g. during collectstatic).
    # Indexes will be created on first actual request via the service layer.
    pass
