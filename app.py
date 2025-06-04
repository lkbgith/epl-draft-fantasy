from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from datetime import datetime
import json
import os
# Add these imports to your app.py
import pandas as pd
import openpyxl

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fantasy_draft.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size


# Create uploads folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)  # <-- Use exist_ok=True

db = SQLAlchemy(app)


# Database Models
class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    # Basic info
    first_name = db.Column(db.String(100))
    second_name = db.Column(db.String(100), nullable=False)
    full_name = db.Column(db.String(200))
    web_name = db.Column(db.String(100))  # Display name

    # Core fields
    team = db.Column(db.String(50), nullable=False)
    position = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(1))  # a=available, i=injured, s=suspended

    # Draft status
    drafted = db.Column(db.Boolean, default=False)
    drafted_by = db.Column(db.Integer, db.ForeignKey('draft_team.id'), nullable=True)

    # Cost and value
    now_cost = db.Column(db.Float, default=0.0)  # Current price

    # Performance stats
    total_points = db.Column(db.Integer, default=0)
    points_per_game = db.Column(db.Float, default=0.0)
    minutes = db.Column(db.Integer, default=0)
    starts = db.Column(db.Integer, default=0)

    # Scoring stats
    goals_scored = db.Column(db.Integer, default=0)
    assists = db.Column(db.Integer, default=0)
    clean_sheets = db.Column(db.Integer, default=0)
    goals_conceded = db.Column(db.Integer, default=0)
    own_goals = db.Column(db.Integer, default=0)
    penalties_saved = db.Column(db.Integer, default=0)
    penalties_missed = db.Column(db.Integer, default=0)

    # Cards
    yellow_cards = db.Column(db.Integer, default=0)
    red_cards = db.Column(db.Integer, default=0)

    # Goalkeeper specific
    saves = db.Column(db.Integer, default=0)

    # Bonus and BPS
    bonus = db.Column(db.Integer, default=0)
    bps = db.Column(db.Integer, default=0)

    # ICT Index
    influence = db.Column(db.Float, default=0.0)
    creativity = db.Column(db.Float, default=0.0)
    threat = db.Column(db.Float, default=0.0)
    ict_index = db.Column(db.Float, default=0.0)

    # Expected stats (xG, xA)
    expected_goals = db.Column(db.Float, default=0.0)
    expected_assists = db.Column(db.Float, default=0.0)
    expected_goal_involvements = db.Column(db.Float, default=0.0)
    expected_goals_conceded = db.Column(db.Float, default=0.0)

    # Per 90 stats
    expected_goals_per_90 = db.Column(db.Float, default=0.0)
    expected_assists_per_90 = db.Column(db.Float, default=0.0)
    saves_per_90 = db.Column(db.Float, default=0.0)
    clean_sheets_per_90 = db.Column(db.Float, default=0.0)

    @property
    def name(self):
        """Display name for the player"""
        return self.web_name or self.second_name

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'full_name': self.full_name,
            'position': self.position,
            'team': self.team,
            'status': self.status,
            'drafted': self.drafted,
            'price': self.now_cost,
            'total_points': self.total_points,
            'points_per_game': self.points_per_game,
            'minutes': self.minutes,
            'goals': self.goals_scored,
            'assists': self.assists,
            'clean_sheets': self.clean_sheets,
            'expected_goals': round(self.expected_goals, 2),
            'expected_assists': round(self.expected_assists, 2),
            'ict_index': self.ict_index
        }

class DraftTeam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    owner = db.Column(db.String(100), nullable=False)
    players = db.relationship('Player', backref='draft_team', lazy=True)

    def get_roster(self):
        roster = {
            'GK': [],
            'DEF': [],
            'MID': [],
            'FWD': []
        }
        for player in self.players:
            roster[player.position].append(player)
        return roster


class Draft(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    current_pick = db.Column(db.Integer, default=1)
    current_team_index = db.Column(db.Integer, default=0)
    total_teams = db.Column(db.Integer)
    is_active = db.Column(db.Boolean, default=True)
    draft_order = db.Column(db.Text)  # JSON string of team IDs


# Routes
@app.route('/')
def index():
    teams = DraftTeam.query.all()
    draft = Draft.query.first()
    return render_template('index.html', teams=teams, draft=draft)


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if request.method == 'POST':
        # Clear existing data
        db.drop_all()
        db.create_all()

        # Create teams
        team_names = request.form.getlist('team_names[]')
        team_owners = request.form.getlist('team_owners[]')

        teams = []
        for name, owner in zip(team_names, team_owners):
            if name and owner:
                team = DraftTeam(name=name, owner=owner)
                db.session.add(team)
                teams.append(team)

        db.session.commit()

        # Create draft
        draft_order = [team.id for team in teams]
        draft = Draft(
            total_teams=len(teams),
            draft_order=json.dumps(draft_order)
        )
        db.session.add(draft)

        # Add sample players (you can expand this)
        sample_players = [
            # Goalkeepers
            {'name': 'Alisson', 'position': 'GK', 'team': 'Liverpool'},
            {'name': 'Ederson', 'position': 'GK', 'team': 'Man City'},
            {'name': 'Ramsdale', 'position': 'GK', 'team': 'Arsenal'},
            # Defenders
            {'name': 'Alexander-Arnold', 'position': 'DEF', 'team': 'Liverpool'},
            {'name': 'Van Dijk', 'position': 'DEF', 'team': 'Liverpool'},
            {'name': 'Cancelo', 'position': 'DEF', 'team': 'Man City'},
            {'name': 'James', 'position': 'DEF', 'team': 'Chelsea'},
            # Midfielders
            {'name': 'Salah', 'position': 'MID', 'team': 'Liverpool'},
            {'name': 'De Bruyne', 'position': 'MID', 'team': 'Man City'},
            {'name': 'Fernandes', 'position': 'MID', 'team': 'Man United'},
            {'name': 'Saka', 'position': 'MID', 'team': 'Arsenal'},
            # Forwards
            {'name': 'Haaland', 'position': 'FWD', 'team': 'Man City'},
            {'name': 'Kane', 'position': 'FWD', 'team': 'Spurs'},
            {'name': 'Jesus', 'position': 'FWD', 'team': 'Arsenal'},
            {'name': 'Darwin', 'position': 'FWD', 'team': 'Liverpool'},
        ]

        for player_data in sample_players:
            player = Player(**player_data)
            db.session.add(player)

        db.session.commit()
        return redirect(url_for('draft'))

    return render_template('setup.html')


@app.route('/draft')
def draft():
    draft = Draft.query.first()
    if not draft:
        return redirect(url_for('setup'))

    teams = DraftTeam.query.all()
    available_players = Player.query.filter_by(drafted=False).all()

    draft_order = json.loads(draft.draft_order)
    current_team_id = draft_order[draft.current_team_index]
    current_team = DraftTeam.query.get(current_team_id)

    return render_template('draft.html',
                           draft=draft,
                           teams=teams,
                           available_players=available_players,
                           current_team=current_team)


@app.route('/draft_player/<int:player_id>', methods=['POST'])
def draft_player(player_id):
    draft = Draft.query.first()
    if not draft or not draft.is_active:
        return jsonify({'error': 'No active draft'}), 400

    player = Player.query.get(player_id)
    if not player or player.drafted:
        return jsonify({'error': 'Player not available'}), 400

    # Get current team
    draft_order = json.loads(draft.draft_order)
    current_team_id = draft_order[draft.current_team_index]
    current_team = DraftTeam.query.get(current_team_id)

    # Draft the player
    player.drafted = True
    player.drafted_by = current_team.id

    # Move to next pick
    draft.current_pick += 1
    draft.current_team_index = (draft.current_team_index + 1) % draft.total_teams

    db.session.commit()

    return redirect(url_for('draft'))


@app.route('/team/<int:team_id>')
def view_team(team_id):
    team = DraftTeam.query.get_or_404(team_id)
    roster = team.get_roster()
    return render_template('team.html', team=team, roster=roster)


# Excel import route
@app.route('/import_excel', methods=['GET', 'POST'])
def import_excel():
    if request.method == 'POST':
        print(f"Upload folder: {app.config.get('UPLOAD_FOLDER')}")  # Debug line
        print(f"Files: {request.files}")  # Debug line
        if 'file' not in request.files:
            return render_template('import_excel.html', error='No file uploaded')

        file = request.files['file']
        if file.filename == '':
            return render_template('import_excel.html', error='No file selected')

        if not file.filename.endswith(('.xlsx', '.xls')):
            return render_template('import_excel.html', error='Please upload an Excel file')

        filepath = None

        try:
            # Save the file temporarily
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            # Import the data
            result = import_fpl_excel(filepath)

            # Clean up
            os.remove(filepath)

            return render_template('import_excel.html', success=True, **result)

        except Exception as e:
            if os.path.exists(filepath):
                os.remove(filepath)
            return render_template('import_excel.html', error=f'Error: {str(e)}')

    # GET request
    current_players = Player.query.count()
    return render_template('import_excel.html', current_players=current_players)


def import_fpl_excel(filepath):
    """Import FPL data from Excel file"""
    imported = 0
    updated = 0
    errors = []

    try:
        # Read the Excel file
        df = pd.read_excel(filepath, sheet_name='Player Data')

        # Map FPL positions to our positions
        position_map = {
            'GKP': 'GK',
            'GK': 'GK',
            'DEF': 'DEF',
            'Def': 'DEF',
            'MID': 'MID',
            'Mid': 'MID',
            'FWD': 'FWD',
            'For': 'FWD',
            'FW': 'FWD'
        }

        for idx, row in df.iterrows():
            try:
                # Get position
                pos = row.get('position', '').strip()
                position = position_map.get(pos, pos.upper())

                if position not in ['GK', 'DEF', 'MID', 'FWD']:
                    errors.append(f"Row {idx + 2}: Unknown position '{pos}'")
                    continue

                # Create web_name (usually last name or known name)
                web_name = row.get('second_name', '').strip()
                if row.get('first_name'):
                    # For some players, use first name (e.g., "Mohamed Salah" -> "Salah")
                    # You might want to customize this logic
                    pass

                # Check if player exists
                existing = Player.query.filter_by(
                    second_name=row.get('second_name', '').strip(),
                    team=row.get('team', '').strip()
                ).first()

                if existing:
                    # Update existing player
                    for field in ['total_points', 'points_per_game', 'minutes', 'goals_scored',
                                  'assists', 'clean_sheets', 'goals_conceded', 'yellow_cards',
                                  'red_cards', 'saves', 'bonus', 'bps', 'influence', 'creativity',
                                  'threat', 'ict_index', 'expected_goals', 'expected_assists',
                                  'expected_goal_involvements', 'expected_goals_conceded',
                                  'expected_goals_per_90', 'expected_assists_per_90',
                                  'saves_per_90', 'clean_sheets_per_90', 'now_cost', 'starts']:
                        if field in row and pd.notna(row[field]):
                            setattr(existing, field, row[field])

                    existing.position = position
                    existing.status = row.get('status', 'a')
                    updated += 1
                else:
                    # Create new player
                    player = Player(
                        first_name=row.get('first_name', '').strip(),
                        second_name=row.get('second_name', '').strip(),
                        full_name=row.get('full_name', '').strip(),
                        web_name=web_name,
                        team=row.get('team', '').strip(),
                        position=position,
                        status=row.get('status', 'a'),
                        drafted=False
                    )

                    # Add all numeric fields
                    for field in ['total_points', 'points_per_game', 'minutes', 'goals_scored',
                                  'assists', 'clean_sheets', 'goals_conceded', 'own_goals',
                                  'penalties_saved', 'penalties_missed', 'yellow_cards',
                                  'red_cards', 'saves', 'bonus', 'bps', 'influence', 'creativity',
                                  'threat', 'ict_index', 'expected_goals', 'expected_assists',
                                  'expected_goal_involvements', 'expected_goals_conceded',
                                  'expected_goals_per_90', 'expected_assists_per_90',
                                  'saves_per_90', 'clean_sheets_per_90', 'now_cost', 'starts']:
                        if field in row and pd.notna(row[field]):
                            setattr(player, field, row[field])

                    db.session.add(player)
                    imported += 1

            except Exception as e:
                errors.append(f"Row {idx + 2}: {str(e)}")
                continue

        db.session.commit()

        return {
            'imported': imported,
            'updated': updated,
            'errors': errors[:10],
            'total_processed': len(df)
        }

    except Exception as e:
        raise Exception(f"Failed to read Excel file: {str(e)}")


# Admin Features

@app.route('/admin/database')
def admin_database():
    """Admin page to inspect database contents"""
    try:
        from sqlalchemy import inspect

        inspector = inspect(db.engine)
        tables = inspector.get_table_names()

        db_info = {}

        for table in tables:
            # Get columns
            columns = inspector.get_columns(table)

            # Get row count
            if table == 'player':
                count = Player.query.count()
                sample = Player.query.limit(5).all()
            elif table == 'draft_team':
                count = DraftTeam.query.count()
                sample = DraftTeam.query.limit(5).all()
            elif table == 'draft':
                count = Draft.query.count()
                sample = Draft.query.limit(5).all()
            else:
                count = 0
                sample = []

            db_info[table] = {
                'columns': columns,
                'count': count,
                'sample': sample
            }

        return render_template('admin_database.html', db_info=db_info, tables=tables)

    except Exception as e:
        return f"Error inspecting database: {str(e)}"


@app.route('/admin/players')
def admin_players():
    """View all players in a table format"""
    players = Player.query.all()
    return render_template('admin_players.html', players=players)


@app.route('/admin/export_db')
def export_database():
    """Export entire database as JSON for backup"""
    try:
        data = {
            'players': [p.to_dict() for p in Player.query.all()],
            'teams': [{
                'id': t.id,
                'name': t.name,
                'owner': t.owner,
                'players': [p.name for p in t.players]
            } for t in DraftTeam.query.all()],
            'draft': []
        }

        draft = Draft.query.first()
        if draft:
            data['draft'] = {
                'current_pick': draft.current_pick,
                'current_team_index': draft.current_team_index,
                'is_active': draft.is_active
            }

        return jsonify(data)

    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/import_players')
def import_players():
    """Basic CSV import page - just redirects to Excel import for now"""
    # For now, just redirect to the Excel import
    return redirect(url_for('import_excel'))

# One-time reset function (safe to keep in code)
@app.route('/admin/reset_database')
def reset_database():
    """Reset database - requires confirmation"""
    confirm = request.args.get('confirm', 'no')

    if confirm != 'yes':
        return """
        <h2>⚠️ Database Reset Confirmation</h2>
        <p><strong>WARNING:</strong> This will delete ALL data in the database!</p>
        <p>Are you sure you want to proceed?</p>
        <a href="/admin/reset_database?confirm=yes" style="background-color: red; color: white; padding: 10px; text-decoration: none;">Yes, Reset Database</a>
        <a href="/" style="background-color: green; color: white; padding: 10px; text-decoration: none; margin-left: 10px;">No, Go Back</a>
        """

    try:
        # Drop all tables
        db.drop_all()
        # Recreate all tables
        db.create_all()
        return """
        <h2>✅ Database Reset Complete!</h2>
        <p>All tables have been recreated with the new schema.</p>
        <p><a href="/">Go to Home</a></p>
        <p><a href="/import_excel">Import Players from Excel</a></p>
        <p><a href="/admin/database">View Database Info</a></p>
        """
    except Exception as e:
        return f"Error resetting database: {str(e)}"

# Create tables
with app.app_context():
    db.create_all()

# HTML Templates (you'll need to create these in a 'templates' folder)
# Here's the structure you'll need:
"""
templates/
    base.html       # Base template with common HTML structure
    index.html      # Home page showing all teams
    setup.html      # Initial setup for creating teams
    draft.html      # Main draft interface
    team.html       # View individual team roster
"""

# Example base.html content:
base_html = '''
<!DOCTYPE html>
<html>
<head>
    <title>EPL Fantasy Draft</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .player-list { margin: 20px 0; }
        .player { padding: 10px; border: 1px solid #ddd; margin: 5px 0; }
        .drafted { background-color: #f0f0f0; opacity: 0.6; }
        .current-pick { background-color: #e3f2fd; padding: 20px; margin: 20px 0; }
        .roster { margin: 20px 0; }
        .position-group { margin: 15px 0; }
    </style>
</head>
<body>
    <h1>EPL Fantasy Draft System</h1>
    {% block content %}{% endblock %}
</body>
</html>
'''

if __name__ == '__main__':
    app.run(debug=True)