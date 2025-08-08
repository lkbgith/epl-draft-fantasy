#!/usr/bin/env bash
# Render build script for EPL Draft Fantasy app

set -o errexit

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Initialize the database and run migrations
# This will create tables and add missing columns
python -c "
from app import app, db, init_and_migrate_db
with app.app_context():
    init_and_migrate_db()
    print('Database initialized and migrated successfully!')
"

echo "Build completed successfully!"