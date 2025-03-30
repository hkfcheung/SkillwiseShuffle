from flask import Flask, render_template, request, redirect, url_for, flash
import random
import pandas as pd
import os
import json
from werkzeug.utils import secure_filename
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Required for flashing messages

players = []

# ---------- Utility Functions ----------


def round_robin_pairings(names):
    n = len(names)
    rounds = []
    players = list(names)
    for i in range(n - 1):
        round = []
        for j in range(n // 2):
            round.append((players[j], players[n - 1 - j]))
        rounds.append(round)
        players = [players[0]] + [players[-1]] + players[1:-1]
    return rounds


def generate_games_all(players):
    skill1 = [name for name, skill in players if skill == 1]
    skill2 = [name for name, skill in players if skill == 2]
    random.shuffle(skill1)
    random.shuffle(skill2)

    teams = []
    while skill1 and skill2:
        teams.append((skill1.pop(), skill2.pop()))

    remaining = skill1 + skill2
    random.shuffle(remaining)
    while len(remaining) >= 2:
        teams.append((remaining.pop(), remaining.pop()))

    games = []
    random.shuffle(teams)
    while len(teams) >= 2:
        team1 = teams.pop()
        team2 = teams.pop()
        games.append((team1, team2))

    if teams:
        games.append((teams.pop(), ))
    return games


def candidate_penalty(candidate_round, team_counts):
    penalty = 0
    new_count = 0
    total_teams = 0
    for game in candidate_round:
        for team in game:
            if len(team) == 2:
                total_teams += 1
                current_count = team_counts.get(frozenset(team), 0)
                penalty += current_count
                if current_count == 0:
                    new_count += 1
    return penalty, new_count, total_teams


def generate_games():
    rounds = {}
    if len(players) == 4:
        names = [p[0] for p in players]
        rr_rounds = round_robin_pairings(names)
        round_num = 1
        for rr in rr_rounds:
            rounds[round_num] = [tuple(rr)]
            round_num += 1

        team_counts = {}
        for r in range(1, 4):
            for team in rounds[r][0]:
                key = frozenset(team)
                team_counts[key] = team_counts.get(key, 0) + 1

        max_attempts = 1000
        best_candidate = None
        best_penalty = float('inf')
        for _ in range(max_attempts):
            candidate_round = generate_games_all(players)
            penalty = 0
            for game in candidate_round:
                for team in game:
                    if len(team) == 2:
                        penalty += team_counts.get(frozenset(team), 0)
            if penalty < best_penalty:
                best_penalty = penalty
                best_candidate = candidate_round
        rounds[4] = best_candidate
    else:
        team_counts = {}
        max_rounds = 4
        max_attempts = 1000

        for round_number in range(1, max_rounds + 1):
            attempt = 0
            best_candidate = None
            best_penalty = float('inf')
            best_new_count = -1
            expected_total = None
            while attempt < max_attempts:
                attempt += 1
                candidate_round = generate_games_all(players)
                if expected_total is None:
                    expected_total = sum(1 for game in candidate_round
                                         for team in game if len(team) == 2)
                penalty, new_count, total_teams = candidate_penalty(
                    candidate_round, team_counts)
                if penalty < best_penalty or (penalty == best_penalty
                                              and new_count > best_new_count):
                    best_penalty = penalty
                    best_new_count = new_count
                    best_candidate = candidate_round
                if new_count == expected_total:
                    best_candidate = candidate_round
                    break
            for game in best_candidate:
                for team in game:
                    if len(team) == 2:
                        key = frozenset(team)
                        team_counts[key] = team_counts.get(key, 0) + 1
            rounds[round_number] = best_candidate
    return rounds


# ---------- Routes ----------


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        name = request.form.get('name')
        skill = int(request.form.get('skill'))
        players.append((name, skill))
        return redirect(url_for('index'))
    return render_template('index.html', players=players)


@app.route('/generate', methods=['POST'])
def generate():
    games = generate_games()
    score_data = {}  # Empty initially
    return render_template('schedule.html',
                           rounds=games,
                           score_data=score_data)


@app.route('/clear', methods=['POST'])
def clear():
    players.clear()
    return redirect(url_for('index'))


@app.route('/import_excel', methods=['POST'])
def import_excel():
    if 'file' not in request.files:
        return redirect(url_for('index'))

    file = request.files['file']
    if file.filename == '':
        return redirect(url_for('index'))

    try:
        df = pd.read_excel(file)
        df.columns = [col.strip().title() for col in df.columns]

        if 'Name' not in df.columns or 'Skill' not in df.columns:
            flash('Excel file must have columns named "Name" and "Skill"')
            return redirect(url_for('index'))

        for _, row in df.iterrows():
            try:
                name = str(row['Name']).strip()
                skill = int(float(row['Skill']))
                if skill in [1, 2] and name:
                    players.append((name, skill))
            except (ValueError, TypeError):
                continue

        flash(f'Successfully imported {len(players)} players')
    except Exception as e:
        flash(f'Error importing Excel file: {str(e)}')

    return redirect(url_for('index'))


@app.route('/save_scores', methods=['POST'])
def save_scores():
    # 1) Get chairs from the form
    chairs = request.form.get('chairs', '').strip()

    # 2) Get email addresses (comma-separated), stripping whitespace
    email_addresses = request.form.get('email_addresses', '').split(',')
    email_addresses = [
        email.strip() for email in email_addresses if email.strip()
    ]

    # 3) Initialize player scores and track rounds/games
    player_scores = {}
    game_keys = {}
    max_rounds = 0

    # 4) Current date
    current_date = datetime.now().strftime("%B %d, %Y")

    # 5) Start building email content
    #    - Add Chairs info right after the tournament name and date
    email_content = f"Friday Night Tournament - {current_date}\n"
    if chairs:
        email_content += f"Chairs: {chairs}\n"
    email_content += "\n"  # extra newline

    # --- Gather all round/game data from the request ---
    for key in request.form:
        if key.startswith('team_r'):
            parts = key.split('_')
            round_num = int(parts[1][1:])
            game_num = int(parts[2][1:])
            if round_num not in game_keys:
                game_keys[round_num] = set()
            game_keys[round_num].add(game_num)
            max_rounds = max(max_rounds, round_num)

    # Parse scores and assign them to player_scores
    for round_num in sorted(game_keys.keys()):
        round_index = round_num - 1  # Convert to 0-based for storing in lists
        for game_num in sorted(game_keys[round_num]):
            team1_members = request.form.get(
                f'team_r{round_num}_g{game_num}_t1', '')
            team2_members = request.form.get(
                f'team_r{round_num}_g{game_num}_t2', '')
            team1_score_str = request.form.get(
                f'score_r{round_num}_g{game_num}_t1', '')
            team2_score_str = request.form.get(
                f'score_r{round_num}_g{game_num}_t2', '')

            team1_score = int(
                team1_score_str) if team1_score_str.strip() else 0
            team2_score = int(
                team2_score_str) if team2_score_str.strip() else 0

            for player in team1_members.split(', '):
                if player not in player_scores:
                    player_scores[player] = [0] * max_rounds
                while len(player_scores[player]) <= round_index:
                    player_scores[player].append(0)
                player_scores[player][round_index] = team1_score

            if team2_members:
                for player in team2_members.split(', '):
                    if player not in player_scores:
                        player_scores[player] = [0] * max_rounds
                    while len(player_scores[player]) <= round_index:
                        player_scores[player].append(0)
                    player_scores[player][round_index] = team2_score

    # --- Build the ASCII table for scores ---
    name_width = 20
    score_width = 10

    # Top border
    email_content += "+" + "-" * name_width
    for _ in range(max_rounds):
        email_content += "+" + "-" * score_width
    email_content += "+" + "-" * score_width + "+\n"

    # Header row
    email_content += f"| {'Name':<{name_width}}"
    for i in range(max_rounds):
        email_content += f"| {'Game ' + str(i+1):<{score_width}}"
    email_content += f"| {'Total':<{score_width}}|\n"

    # Separator under header
    email_content += "+" + "-" * name_width
    for _ in range(max_rounds):
        email_content += "+" + "-" * score_width
    email_content += "+" + "-" * score_width + "+\n"

    # Rows for each player
    for player, scores in sorted(player_scores.items()):
        total = sum(score for score in scores if score is not None)
        email_content += f"| {player:<{name_width}}"
        for score in scores:
            score_str = str(score) if score is not None else ""
            email_content += f"| {score_str:<{score_width}}"
        email_content += f"| {total:<{score_width}}|\n"

    # Bottom border
    email_content += "+" + "-" * name_width
    for _ in range(max_rounds):
        email_content += "+" + "-" * score_width
    email_content += "+" + "-" * score_width + "+\n"

    # --- Send Email if addresses are provided ---
    if email_addresses:
        try:
            msg = MIMEMultipart()
            msg['Subject'] = 'Skillwise Shuffle - Game Results'
            msg['From'] = os.environ.get('GMAIL_USER', 'default@example.com')
            msg['To'] = ', '.join(email_addresses)

            # Convert ASCII table to HTML <pre>
            msg.attach(
                MIMEText(
                    f"<pre style='font-family: monospace'>{email_content}</pre>",
                    'html'))

            smtp_server = "smtp.gmail.com"
            smtp_port = 587
            sender_email = os.environ.get('GMAIL_USER')
            password = os.environ.get('GMAIL_PASSWORD')

            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(sender_email, password)
            server.send_message(msg)
            server.quit()

            flash('Scores saved and email sent successfully!')
        except Exception as e:
            flash(f'Scores saved but email sending failed: {str(e)}')
    else:
        flash('Scores saved successfully (no email addresses provided).')

    # --- Restore original schedule and re-render ---
    original_rounds_json = request.form.get('original_rounds_json')
    score_data = {}
    for key in request.form:
        if key.startswith('score_r') or key.startswith('team_r'):
            score_data[key] = request.form.get(key)

    try:
        rounds = json.loads(
            original_rounds_json) if original_rounds_json else {}
    except Exception as e:
        flash('Error restoring original schedule, returning to index.')
        return redirect(url_for('index'))

    return render_template('schedule.html',
                           rounds=rounds,
                           score_data=score_data)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
