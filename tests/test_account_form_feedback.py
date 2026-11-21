"""Exercise real account-form JavaScript events without network or passwords."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync(process.argv[1], 'utf8');
function fixture(mode='register') {
  const windowEvents={}, formEvents={}, toggleEvents={}, timers=new Map();
  let valid=true, nextTimer=0;
  function element() { return {attributes:{}, setAttribute(k,v){this.attributes[k]=v;}, removeAttribute(k){delete this.attributes[k];}}; }
  const button={...element(),disabled:false,textContent:mode==='register'?'Create account and continue':'Sign in and continue'};
  const status={textContent:''};
  const fields={email:{value:'synthetic@example.test',disabled:false}, password:{value:'synthetic-password-123',disabled:false}, _csrf:{value:'synthetic-csrf',disabled:false}, mode:{value:mode,disabled:false}};
  const form={...element(),dataset:{accountMode:mode},checkValidity:()=>valid,addEventListener:(name,fn)=>formEvents[name]=fn};
  const password={...fields.password,type:'password'};
  const toggle={...element(),textContent:'Show password',addEventListener:(name,fn)=>toggleEvents[name]=fn};
  const nodes={'account-form':form,'account-submit':button,'account-status':status,password,'show-password':toggle};
  const window={addEventListener:(name,fn)=>windowEvents[name]=fn,setTimeout:(fn,delay)=>{assert.equal(delay,8000);timers.set(++nextTimer,fn);return nextTimer;},clearTimeout:id=>timers.delete(id)};
  const context={document:{getElementById:id=>nodes[id]},window};
  Object.defineProperty(context,'localStorage',{get(){throw new Error('Credentials must not be stored');}});
  context.fetch=()=>{throw new Error('Native form submission must not be replaced');};
  vm.runInNewContext(script,context);
  return {button,status,fields,form,password,toggle,timers,
    valid:v=>valid=v,
    submit:()=>{const event={defaultPrevented:false,preventDefault(){this.defaultPrevented=true;}};formEvents.submit(event);return event;},
    show:()=>windowEvents.pageshow(),hide:()=>windowEvents.pagehide(),togglePassword:()=>toggleEvents.click()};
}
'''


@unittest.skipUnless(shutil.which('node'), 'Node is needed for account event behavior checks')
class AccountFormFeedbackTests(unittest.TestCase):
    def run_behavior(self, assertions):
        result = subprocess.run(
            ['node', '-e', HARNESS + assertions, str(ROOT/'static/account.js')],
            text=True, capture_output=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_submit_remains_single_and_keeps_all_form_fields(self):
        self.run_behavior(r'''
const p=fixture();
assert.equal(p.submit().defaultPrevented,false);
assert.equal(p.button.disabled,true);
assert.equal(p.status.textContent,'Creating your account…');
assert.equal(p.form.attributes['aria-busy'],'true');
assert.equal(p.submit().defaultPrevented,true);
assert.ok(Object.values(p.fields).every(field=>field.disabled===false));
assert.equal(p.fields._csrf.value,'synthetic-csrf');
assert.equal(p.fields.password.value,'synthetic-password-123');
''')

    def test_slow_request_explains_wait_without_retrying_then_back_restores(self):
        self.run_behavior(r'''
const p=fixture('login');
p.submit();
assert.equal(p.status.textContent,'Signing you in…');
for(const fn of p.timers.values())fn();
assert.match(p.status.textContent,/taking longer than usual/);
assert.equal(p.button.disabled,true);
p.show();
assert.equal(p.button.disabled,false);
assert.equal(p.status.textContent,'');
assert.equal(p.form.attributes['aria-busy'],undefined);
assert.equal(p.timers.size,0);
assert.equal(p.submit().defaultPrevented,false);
''')

    def test_invalid_form_stays_usable_and_departure_cancels_feedback_timer(self):
        self.run_behavior(r'''
const p=fixture();p.valid(false);p.submit();
assert.equal(p.button.disabled,false);
assert.equal(p.status.textContent,'');
assert.equal(p.timers.size,0);
p.valid(true);p.submit();p.hide();
assert.equal(p.timers.size,0);
''')

    def test_password_visibility_control_is_preserved(self):
        self.run_behavior(r'''
const p=fixture();p.togglePassword();
assert.equal(p.password.type,'text');
assert.equal(p.toggle.attributes['aria-pressed'],'true');
assert.equal(p.toggle.textContent,'Hide password');
p.togglePassword();
assert.equal(p.password.type,'password');
assert.equal(p.toggle.attributes['aria-pressed'],'false');
''')


if __name__ == '__main__':
    unittest.main()
