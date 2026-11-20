/* Offline regression: Leaflet moveend may precede GridLayer tile initialization. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

let ready = false;
const tileStates = [];
const bridge = {
  mapReady() { ready = true; },
  tilesReady(value) { tileStates.push(value); },
  tilesFailed() {},
};
const mapHandlers = {};
const tileHandlers = {};
const map = {
  on(event, handler) { mapHandlers[event] = handler; return this; },
  setView() { mapHandlers.moveend(); return this; },
};
const tiles = {
  // No _tiles yet, matching Leaflet's first moveend before the layer is loaded.
  on(event, handler) { tileHandlers[event] = handler; return this; },
  addTo() { return this; },
};
const context = {
  L: { map() { return map; }, tileLayer() { return tiles; } },
  qt: { webChannelTransport: {} },
  QWebChannel: function (_transport, callback) { callback({objects: {areaBridge: bridge}}); },
  document: {getElementById() { return {}; }, addEventListener() {}},
  window: {},
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../map.js"), "utf8"), context);
assert.equal(ready, true, "Initialization must reach mapReady before tile storage exists");
assert.deepEqual(tileStates, [false]);
assert.equal(typeof context.window.TrafficForgeArea.draw, "function");
tileHandlers.load();
assert.equal(tileStates.at(-1), false);
tiles._tiles = {first: {current:true, el:{complete:true, naturalWidth:256}}};
tileHandlers.load();
assert.equal(tileStates.at(-1), true);
tiles._tiles.second = {current:true, el:{complete:false, naturalWidth:0}};
tileHandlers.load();
assert.equal(tileStates.at(-1), false, "Partial tiles must not enable confirmation");
console.log("Map initialization and visible-tile state regression passed.");
