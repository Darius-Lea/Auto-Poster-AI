"""Public, plain-language support and accurate current data/feature disclosures."""
import os
from flask import Blueprint,render_template,abort,jsonify
help_bp=Blueprint('help',__name__)

def support():
 value=os.getenv('AUTOPOST_SUPPORT_EMAIL','').strip()
 return value if '@' in value and not any(c in value for c in '\r\n<>"') else ''

@help_bp.route('/help')
def guide():return render_template('help.html',support_email=support())

@help_bp.route('/privacy')
def privacy():return render_template('service_policy.html',kind='privacy',support_email=support(),operator_name=os.getenv('AUTOPOST_OPERATOR_NAME','').strip())

@help_bp.route('/terms')
def terms():return render_template('service_policy.html',kind='terms',support_email=support(),operator_name=os.getenv('AUTOPOST_OPERATOR_NAME','').strip())

@help_bp.route('/readiness')
def readiness():
 from app_mvp import current_workspace
 if current_workspace()!='owner':abort(403)
 import service_readiness
 return jsonify(service_readiness.configured())
