// Executes the dashboard's inline script against a stub DOM; exits non-zero on any runtime exception.
// usage: node dom_smoke.js <index.html>
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const m = html.match(/<script>([\s\S]*)<\/script>/);
if (!m) { console.error('no script'); process.exit(2); }
const els = {};
const mk = () => ({
  _h: '', value: '', checked: true, dataset: {}, textContent: '',
  set innerHTML(v) { this._h = v; }, get innerHTML() { return this._h; },
  classList: { toggle() {}, add() {}, remove() {} },
  addEventListener() {}, insertAdjacentHTML(_, h) { this._h += h; },
});
global.document = {
  getElementById: id => els[id] || (els[id] = mk()),
  querySelectorAll: () => [],
  querySelector: () => ({}),
};
global.location = { hash: '' };
try {
  eval(m[1]);
} catch (e) { console.error('RUNTIME ERROR:', e.stack); process.exit(1); }
const need = ['head', 'tiles', 'tier', 'line', 'paths', 'plan', 'tbl'];
for (const n of need) if (!els[n] || !els[n]._h) { console.error('empty section', n); process.exit(1); }
const out = {};
for (const k of Object.keys(els)) out[k] = els[k]._h.length;
console.log(JSON.stringify(out));
