from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
# from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename
from datetime import datetime
import json
import os
import pandas as pd
import numpy as np
import openpyxl

if 'DATABASE_URL' in os.environ:
    # Fix for SQLAlchemy
    database_url = os.environ['DATABASE_URL']
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://')
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # Local development
    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'fantasy_draft.db')


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-here')
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'fantasy_draft.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size


# Create uploads folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)  # <-- Use exist_ok=True

db = SQLAlchemy(app)
# socketio = SocketIO(app)


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

    def get_team_counts(self):
        """Count how many players from each EPL team"""
        team_counts = {}
        for player in self.players:
            team_counts[player.team] = team_counts.get(player.team, 0) + 1
        return team_counts

    def can_draft_player(self, player):
        """Check if this player can be drafted based on constraints"""
        # Check team limit (max 3 from same team)
        team_counts = self.get_team_counts()
        if team_counts.get(player.team, 0) >= 3:
            return False, f"Already have 3 players from {player.team}"

        # Check position limits
        roster = self.get_roster()
        position_limits = {
            'GK': 2,
            'DEF': 5,
            'MID': 5,
            'FWD': 3
        }

        if len(roster[player.position]) >= position_limits[player.position]:
            return False, f"Already have {position_limits[player.position]} {player.position}s"

        return True, "OK"


class Draft(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    current_pick = db.Column(db.Integer, default=1)
    current_team_index = db.Column(db.Integer, default=0)
    total_teams = db.Column(db.Integer)
    is_active = db.Column(db.Boolean, default=True)
    draft_order = db.Column(db.Text)  # JSON string of team IDs
    is_snake_draft = db.Column(db.Boolean, default=True)  # New field!

    @property
    def current_round(self):
        """Calculate what round we're in"""
        if self.total_teams == 0:
            return 1
        return ((self.current_pick - 1) // self.total_teams) + 1

    @property
    def is_reverse_round(self):
        """Check if this round should go in reverse order"""
        return self.is_snake_draft and (self.current_round % 2 == 0)

    def get_current_team_id(self):
        """Get the current team ID considering snake draft"""
        draft_order = json.loads(self.draft_order)

        if self.is_reverse_round:
            # Reverse the order for even rounds
            actual_index = self.total_teams - 1 - self.current_team_index
            return draft_order[actual_index]
        else:
            return draft_order[self.current_team_index]

    def advance_to_next_pick(self):
        """Move to the next pick in snake draft order"""
        self.current_pick += 1
        self.current_team_index += 1

        # Reset team index when we complete a round
        if self.current_team_index >= self.total_teams:
            self.current_team_index = 0


class Wishlist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('draft_team.id'), nullable=False)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=False)
    rank = db.Column(db.Integer, nullable=False)  # 1 = highest priority
    position_filter = db.Column(db.String(20))  # Optional: filter by position
    notes = db.Column(db.String(200))  # Optional notes
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    team = db.relationship('DraftTeam', backref='wishlist_items')
    player = db.relationship('Player', backref='wishlist_entries')

    # Ensure unique player per team
    __table_args__ = (db.UniqueConstraint('team_id', 'player_id'),)


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
        DraftTeam.query.delete()
        Draft.query.delete()

        # Reset all players to undrafted status
        Player.query.update({'drafted': False, 'drafted_by': None})
        db.session.commit()

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

        db.session.commit()
        return redirect(url_for('draft'))

    return render_template('setup.html')


@app.route('/draft')
def draft():
    draft = Draft.query.first()
    if not draft:
        return redirect(url_for('setup'))

    teams = DraftTeam.query.all()

    # Get sorting preference from URL parameters
    sort_by = request.args.get('sort', 'total_points')
    position_filter = request.args.get('position', 'all')

    # Build query for available players
    query = Player.query.filter_by(drafted=False)

    # Apply position filter
    if position_filter != 'all':
        query = query.filter_by(position=position_filter)

    # Apply sorting
    from sqlalchemy import desc, nullslast

    if sort_by == 'name':
        query = query.order_by(Player.second_name)
    elif sort_by == 'total_points':
        query = query.order_by(nullslast(desc(Player.total_points)))
    elif sort_by == 'points_per_game':
        query = query.order_by(nullslast(desc(Player.points_per_game)))
    elif sort_by == 'now_cost':
        query = query.order_by(nullslast(desc(Player.now_cost)))
    elif sort_by == 'goals_scored':
        query = query.order_by(nullslast(desc(Player.goals_scored)))
    elif sort_by == 'assists':
        query = query.order_by(nullslast(desc(Player.assists)))
    elif sort_by == 'minutes':
        query = query.order_by(nullslast(desc(Player.minutes)))
    else:
        query = query.order_by(nullslast(desc(Player.total_points)))

    available_players = query.all()

    # Get current team using snake draft logic
    current_team_id = draft.get_current_team_id()
    current_team = DraftTeam.query.get(current_team_id)

    # Create draft order display
    draft_order_ids = json.loads(draft.draft_order)
    if draft.is_reverse_round:
        # Show reversed order for even rounds
        display_order = list(reversed(draft_order_ids))
    else:
        display_order = draft_order_ids

    # Get team objects in display order
    display_teams = [DraftTeam.query.get(team_id) for team_id in display_order]

    return render_template('draft.html',
                           draft=draft,
                           teams=teams,
                           available_players=available_players,
                           current_team=current_team,
                           current_sort=sort_by,
                           current_position=position_filter,
                           display_teams=display_teams,
                           current_round=draft.current_round,
                           is_reverse_round=draft.is_reverse_round)


@app.route('/draft_player/<int:player_id>', methods=['POST'])
def draft_player(player_id):
    draft = Draft.query.first()
    if not draft or not draft.is_active:
        return jsonify({'error': 'No active draft'}), 400

    player = Player.query.get(player_id)
    if not player or player.drafted:
        return jsonify({'error': 'Player not available'}), 400

    # Get current team using snake draft logic
    current_team_id = draft.get_current_team_id()
    current_team = DraftTeam.query.get(current_team_id)

    # CHECK DRAFT CONSTRAINTS
    can_draft, reason = current_team.can_draft_player(player)
    if not can_draft:
        # Flash message and redirect back
        from flask import flash
        flash(f"Cannot draft {player.name}: {reason}", 'error')
        return redirect(url_for('draft'))

    # Draft the player
    player.drafted = True
    player.drafted_by = current_team.id

    # Check which teams had this player on their wishlist
    wishlist_entries = Wishlist.query.filter_by(player_id=player_id).all()
    affected_teams = []
    for entry in wishlist_entries:
        if entry.team_id != current_team_id:
            affected_teams.append({
                'team_id': entry.team_id,
                'team_name': entry.team.name,
                'rank': entry.rank
            })

    # Advance to next pick
    draft.advance_to_next_pick()

    db.session.commit()

    # Emit updates
    #socketio.emit('player_drafted', {
    #    'player_name': player.name,
    #    'player_id': player.id,
    #    'team_name': current_team.name,
    #    'next_team_id': draft.get_current_team_id()
    #})

    #if affected_teams:
    #    socketio.emit('wishlist_player_drafted', {
    #        'player_name': player.name,
    #        'player_id': player.id,
    #        'drafted_by': current_team.name,
    #        'affected_teams': affected_teams
    #    })

    return redirect(url_for('draft'))


@app.route('/team/<int:team_id>/wishlist')
def team_wishlist(team_id):
    team = DraftTeam.query.get_or_404(team_id)

    # Get sorting and filtering preferences from URL parameters
    sort_by = request.args.get('sort', 'total_points')
    position_filter = request.args.get('position', 'all')

    # Get wishlist items ordered by rank
    wishlist = Wishlist.query.filter_by(team_id=team_id).order_by(Wishlist.rank).all()

    # Get available players for adding to wishlist
    wishlisted_player_ids = [w.player_id for w in wishlist]

    # Build query for available players
    query = Player.query.filter(Player.drafted == False)

    # Exclude already wishlisted players
    if wishlisted_player_ids:
        query = query.filter(~Player.id.in_(wishlisted_player_ids))

    # Apply position filter
    if position_filter != 'all':
        query = query.filter_by(position=position_filter)

    # Apply sorting
    from sqlalchemy import desc, nullslast

    if sort_by == 'name':
        query = query.order_by(Player.second_name)
    elif sort_by == 'total_points':
        query = query.order_by(nullslast(desc(Player.total_points)))
    elif sort_by == 'points_per_game':
        query = query.order_by(nullslast(desc(Player.points_per_game)))
    elif sort_by == 'now_cost':
        query = query.order_by(nullslast(desc(Player.now_cost)))
    elif sort_by == 'goals_scored':
        query = query.order_by(nullslast(desc(Player.goals_scored)))
    elif sort_by == 'assists':
        query = query.order_by(nullslast(desc(Player.assists)))
    elif sort_by == 'minutes':
        query = query.order_by(nullslast(desc(Player.minutes)))
    else:
        query = query.order_by(nullslast(desc(Player.total_points)))

    available_players = query.all()

    return render_template('wishlist.html',
                           team=team,
                           wishlist=wishlist,
                           available_players=available_players,
                           current_sort=sort_by,
                           current_position=position_filter)


@app.route('/team/<int:team_id>/wishlist/add/<int:player_id>', methods=['POST'])
def add_to_wishlist(team_id, player_id):
    team = DraftTeam.query.get_or_404(team_id)
    player = Player.query.get_or_404(player_id)

    # Check if already in wishlist
    existing = Wishlist.query.filter_by(team_id=team_id, player_id=player_id).first()
    if existing:
        return jsonify({'error': 'Player already in wishlist'}), 400

    # Get next rank number
    max_rank = db.session.query(db.func.max(Wishlist.rank)).filter_by(team_id=team_id).scalar() or 0

    # Add to wishlist
    wishlist_item = Wishlist(
        team_id=team_id,
        player_id=player_id,
        rank=max_rank + 1
    )
    db.session.add(wishlist_item)
    db.session.commit()

    return redirect(url_for('team_wishlist', team_id=team_id))


@app.route('/team/<int:team_id>/wishlist/remove/<int:player_id>', methods=['POST'])
def remove_from_wishlist(team_id, player_id):
    wishlist_item = Wishlist.query.filter_by(team_id=team_id, player_id=player_id).first_or_404()

    # Get items that need to move up in rank
    items_to_update = Wishlist.query.filter(
        Wishlist.team_id == team_id,
        Wishlist.rank > wishlist_item.rank
    ).all()

    # Remove the item
    db.session.delete(wishlist_item)

    # Update ranks
    for item in items_to_update:
        item.rank -= 1

    db.session.commit()
    return redirect(url_for('team_wishlist', team_id=team_id))


@app.route('/team/<int:team_id>/wishlist/reorder', methods=['POST'])
def reorder_wishlist(team_id):
    """Update wishlist order via drag and drop"""
    new_order = request.json.get('order', [])

    for index, player_id in enumerate(new_order):
        Wishlist.query.filter_by(
            team_id=team_id,
            player_id=player_id
        ).update({'rank': index + 1})

    db.session.commit()
    return jsonify({'success': True})


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


# Update your import_fpl_excel function in app.py

def import_fpl_excel(filepath):
    """Import FPL data from Excel file - handles both old and new formats"""
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

                # Handle name fields - new format has full_name, old format has first_name/second_name
                if 'full_name' in row and pd.notna(row['full_name']):
                    # New format - split full_name
                    full_name = str(row['full_name']).strip()
                    name_parts = full_name.split()

                    if len(name_parts) >= 2:
                        first_name = ' '.join(name_parts[:-1])  # Everything except last word
                        second_name = name_parts[-1]  # Last word
                    else:
                        first_name = ''
                        second_name = full_name

                    web_name = second_name  # Use last name as display name
                else:
                    # Old format
                    first_name = str(row.get('first_name', '')).strip()
                    second_name = str(row.get('second_name', '')).strip()
                    full_name = f"{first_name} {second_name}".strip()
                    web_name = second_name

                # Check if player exists
                existing = Player.query.filter_by(
                    second_name=second_name,
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
                                  'saves_per_90', 'clean_sheets_per_90', 'starts']:
                        if field in row and pd.notna(row[field]):
                            setattr(existing, field, row[field])

                    # Update price field (might be 'now_cost' or 'price')
                    if 'price' in row and pd.notna(row['price']):
                        existing.now_cost = float(row['price'])
                    elif 'now_cost' in row and pd.notna(row['now_cost']):
                        existing.now_cost = float(row['now_cost'])

                    existing.position = position
                    existing.status = row.get('status', 'Available')
                    existing.full_name = full_name
                    existing.first_name = first_name
                    existing.web_name = web_name
                    updated += 1
                else:
                    # Create new player
                    player = Player(
                        first_name=first_name,
                        second_name=second_name,
                        full_name=full_name,
                        web_name=web_name,
                        team=row.get('team', '').strip(),
                        position=position,
                        status=row.get('status', 'Available'),
                        drafted=False
                    )

                    # Add all numeric fields
                    for field in ['total_points', 'points_per_game', 'minutes', 'goals_scored',
                                  'assists', 'clean_sheets', 'goals_conceded', 'own_goals',
                                  'penalties_saved', 'penalties_missed', 'yellow_cards',
                                  'red_cards', 'saves', 'bonus', 'influence', 'creativity',
                                  'threat', 'ict_index', 'starts']:
                        if field in row and pd.notna(row[field]):
                            setattr(player, field, row[field])

                    # Handle price field
                    if 'price' in row and pd.notna(row['price']):
                        player.now_cost = float(row['price'])
                    elif 'now_cost' in row and pd.notna(row['now_cost']):
                        player.now_cost = float(row['now_cost'])

                    # Handle BPS field if it exists
                    if 'bps' in row and pd.notna(row['bps']):
                        player.bps = int(row['bps'])
                    elif 'bonus' in row and pd.notna(row['bonus']):
                        # If no bps but has bonus, estimate bps
                        player.bps = int(row['bonus']) * 3

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

@app.route('/debug_excel')
def debug_excel():
    """Debug route to check Excel file structure"""
    try:
        # Path to your uploaded Excel file
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'Draft Board 2024.xlsx')

        if not os.path.exists(filepath):
            return "No Excel file found. Please upload it first."

        # Read Excel file
        df = pd.read_excel(filepath, sheet_name='Player Data')

        # Get info about the dataframe
        info = {
            'columns': df.columns.tolist(),
            'shape': f"{len(df)} rows, {len(df.columns)} columns",
            'first_row': df.iloc[0].to_dict() if len(df) > 0 else {},
            'sample_data': []
        }

        # Get sample of first 3 players
        for i in range(min(3, len(df))):
            player_data = df.iloc[i].to_dict()
            # Convert numpy types to Python types for JSON serialization
            clean_data = {}
            for k, v in player_data.items():
                if pd.isna(v):
                    clean_data[k] = None
                elif isinstance(v, (np.integer, np.int64)):
                    clean_data[k] = int(v)
                elif isinstance(v, (np.floating, np.float64)):
                    clean_data[k] = float(v)
                else:
                    clean_data[k] = str(v)
            info['sample_data'].append(clean_data)

        return f"<pre>{json.dumps(info, indent=2)}</pre>"

    except Exception as e:
        return f"Error: {str(e)}"


@app.route('/check_player_stats')
def check_player_stats():
    """Check what stats are actually in the database"""
    players = Player.query.limit(5).all()

    if not players:
        return "No players in database!"

    output = "<h2>Player Stats Check</h2>"

    for player in players:
        output += f"<h3>{player.name} - {player.team}</h3>"
        output += "<ul>"

        # Check all the stats fields
        stats_fields = ['total_points', 'points_per_game', 'minutes', 'goals_scored',
                        'assists', 'clean_sheets', 'expected_goals', 'now_cost']

        for field in stats_fields:
            value = getattr(player, field, 'FIELD MISSING')
            output += f"<li>{field}: {value}</li>"

        output += "</ul>"

    return output

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
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))