// 计算引擎回归校核（node static/earthwork/engine.test.mjs [model.json]）：
// 套用图纸方案（北侧 1841.64、由南向北 2.0%）须复现已与《土方平衡图》《护坡挡墙布置图》工程量表校核过的结果。
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createEngine } from './engine.js';

const path = process.argv.slice(2).find(a => !a.startsWith('--')) || fileURLToPath(new URL('../../earthwork_data/demo/model.json', import.meta.url));
const engine = createEngine(JSON.parse(readFileSync(path, 'utf-8')));
const d = engine.drawingDesign(), r = engine.compute(d.H, d.iL, d.iT, 'rule'), V = r.V;
const got = {
  H: d.H, inCut: Math.round(V.inCut), inFill: Math.round(V.inFill), outCut: Math.round(V.outCut), outFill: Math.round(V.outFill),
  wall: Math.round(V.wFV + V.wYV), slopeA: Math.round(V.slopeA), slopeL: Math.round(V.slopeL), total: Math.round(r.total),
};
const expected = { inCut: 64864, inFill: 34341, outCut: 5523, outFill: 0, wall: 6611, slopeA: 1160, slopeL: 171 };
const failed = Object.entries(expected).filter(([k, v]) => Math.abs(got[k] - v) > 1)   // 允许 1 的取整差.map(([k, v]) => `${k}: 期望 ${v}，实际 ${got[k]}`);
const scan = engine.scan(0, 0, 'rule');
got.flatBestH = scan.best.H; got.flatBestTotal = Math.round(scan.best.total); got.balanceH = scan.balance.H;
if (process.argv.includes('--optimize')) {
  const o = await engine.optimize3('rule');
  got.opt = { H: +o.best.H.toFixed(2), iL: o.best.iL, iT: o.best.iT, total: Math.round(o.best.total), evaluations: o.evaluations };
}
console.log(JSON.stringify(got));
if (failed.length) { console.error('回归校核未通过：' + failed.join('；')); process.exit(1); }
