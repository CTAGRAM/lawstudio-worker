// Shared login-form heuristics so the planner (plan_flow.mjs) and the recorder
// (recorder.mjs) detect and fill the SAME fields — the user only supplies an
// email + password, no CSS selectors.
export const USER_SEL =
  'input[type=email]:visible, input[name*=email i]:visible, input[id*=email i]:visible, ' +
  'input[name*=user i]:visible, input[id*=user i]:visible, input[autocomplete="username"]:visible, ' +
  'input[type=text]:visible';
export const PASS_SEL = 'input[type=password]:visible';
export const SUBMIT_SEL =
  'button[type=submit]:visible, button:has-text("Log in"), button:has-text("Sign in"), ' +
  'button:has-text("Continue"), button:has-text("Log In"), button:has-text("Sign In")';
export const LOGIN_LINKS = [
  'a:has-text("Log in")', 'a:has-text("Login")', 'a:has-text("Sign in")',
  'button:has-text("Log in")', 'button:has-text("Login")', 'button:has-text("Sign in")'];

// Plain auto-login (no cursor visuals) for the planner: navigate, reveal the
// form if needed, fill, submit. Returns nothing; failures are swallowed so the
// planner still produces a plan from whatever page it lands on.
export async function autoLogin(page, a) {
  await page.goto(a.loginUrl || a.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForTimeout(900);
  const hasPass = async () => (await page.locator(PASS_SEL).count().catch(() => 0)) > 0;
  if (!(await hasPass())) {
    for (const sel of LOGIN_LINKS) {
      try { const b = page.locator(sel).first();
        if (await b.isVisible({ timeout: 300 })) { await b.click({ timeout: 2500 }); await page.waitForTimeout(1400); break; } } catch {}
    }
  }
  try { const uf = page.locator(a.userSel || USER_SEL).first(); await uf.waitFor({ timeout: 8000 });
    await uf.click({ timeout: 3000 }); await page.keyboard.type(a.user || '', { delay: 20 }); } catch {}
  try { const pf = page.locator(a.passSel || PASS_SEL).first(); await pf.waitFor({ timeout: 6000 });
    await pf.click({ timeout: 3000 }); await page.keyboard.type(a.pass || '', { delay: 20 }); } catch {}
  try { await page.locator(a.submitSel || SUBMIT_SEL).first().click({ timeout: 4000 }); }
  catch { try { await page.keyboard.press('Enter'); } catch {} }
  if (a.successUrl) await page.waitForURL(a.successUrl, { timeout: 20000 }).catch(() => {});
  else await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1200);
}
