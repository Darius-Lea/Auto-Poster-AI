"""Configuration evidence only: never confuse configured secrets with live validation."""
import os
from urllib.parse import urlparse

def configured():
 import meta_accounts,ai_providers,editor_connectors
 try:
  import account_support
  mail=bool(account_support.mail_configured())
 except (ImportError,ValueError):mail=False
 origin=os.getenv('AUTOPOST_PUBLIC_URL','').rstrip('/')
 try:
  u=urlparse(origin);origin_ok=u.scheme=='https' and bool(u.hostname) and not u.username and not u.password and not u.query and not u.fragment and u.path in ('','/')
 except ValueError:origin_ok=False
 try:facebook=bool(meta_accounts.configured())
 except (ValueError,RuntimeError):facebook=False
 billing=bool(os.getenv('STRIPE_SECRET_KEY') and os.getenv('STRIPE_WEBHOOK_SECRET') and os.getenv('STRIPE_BASIC_PRICE_ID') and origin_ok)
 ai=any(p['configured'] for p in ai_providers.provider_status()['providers'])
 public_sales=os.getenv('AUTOPOST_PUBLIC_SALES_ENABLED','false').lower()=='true'
 support=os.getenv('AUTOPOST_SUPPORT_EMAIL','').strip();support_ok='@' in support and '\n' not in support and '\r' not in support
 verified=os.getenv('AUTOPOST_RELEASE_VERIFIED','false').lower()=='true'
 durable=os.getenv('AUTOPOST_DURABLE_STORAGE_VERIFIED','false').lower()=='true'
 checks={'https_origin':origin_ok,'stable_sessions':len(os.getenv('AUTOPOST_WEB_SECRET',''))>=32,'facebook_connection':facebook,'subscription_service':billing,'password_recovery_mail':mail,'support_contact':support_ok,'operator_identity':len(os.getenv('AUTOPOST_OPERATOR_NAME','').strip())>=2,'commercial_terms_operator_attested':os.getenv('AUTOPOST_LEGAL_REVIEW_VERIFIED','false').lower()=='true','durable_storage_operator_attested':durable,'live_release_operator_attested':verified}
 return {'checks':checks,'public_sales_enabled':public_sales,'configuration_complete':all(checks.values()),'release_enabled_by_operator':public_sales and all(checks.values()),'managed_ai_configured':ai,'notice':'These checks show configuration and explicit operator attestations, not an independent live deployment or security certification.'}
