/* User-driven viewport only: no prefetch, offline downloads, or background map scans. */
(() => {
  "use strict";
  let bridge, map, tiles, rectangle, start, drawing = false, language = "en";
  const hint = document.getElementById("hint");
  const copy = (zh, en) => language === "zh" ? zh : en;
  function message() {
    hint.textContent = drawing
      ? copy("按住鼠标拖出矩形，松开完成框选", "Drag a rectangle, then release to select")
      : copy("拖动地图或滚轮缩放，然后点击“绘制区域”", "Pan or zoom, then click Draw area");
  }
  function emitBounds() {
    const b = rectangle.getBounds();
    bridge.boundsChanged(JSON.stringify({west:b.getWest(),south:b.getSouth(),east:b.getEast(),north:b.getNorth()}));
  }
  function drawMode(value) {
    drawing = Boolean(value);
    start = null;
    if (drawing) { map.dragging.disable(); map.doubleClickZoom.disable(); }
    else { map.dragging.enable(); map.doubleClickZoom.enable(); }
    map.getContainer().classList.toggle("drawing", drawing);
    message();
  }
  function rectangleFor(bounds) {
    if (rectangle) rectangle.remove();
    rectangle = L.rectangle(bounds, {color:"#5664e8",weight:3,fillColor:"#6777f1",fillOpacity:0.16}).addTo(map);
  }
  function complete(event) {
    if (!start) return;
    const end = map.mouseEventToLatLng(event);
    rectangleFor(L.latLngBounds(start, end));
    drawMode(false);
    bridge.drawingFinished();
    emitBounds();
  }
  function tileState() {
    // Leaflet emits the initial moveend before GridLayer.onAdd creates its tile store.
    const visible = Object.values(tiles._tiles || {}).filter(tile => tile.current);
    const ready = visible.length > 0 && visible.every(tile =>
      tile.el.complete && tile.el.naturalWidth > 0);
    bridge.tilesReady(ready);
  }
  new QWebChannel(qt.webChannelTransport, channel => {
    bridge = channel.objects.areaBridge;
    map = L.map("map", {worldCopyJump:false, maxBounds:[[-84.99,-180],[84.99,180]],
      maxBoundsViscosity:1, minZoom:3, maxZoom:19, boxZoom:false});
    tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom:19, noWrap:true, keepBuffer:0, updateWhenIdle:true
    });
    tiles.on("loading", () => bridge.tilesReady(false));
    tiles.on("load", () => {
      tileState();
      if (Object.values(tiles._tiles || {}).some(tile => tile.current && tile.el.complete && !tile.el.naturalWidth)) {
        bridge.tilesFailed();
      }
    });
    tiles.on("tileerror", () => bridge.tilesFailed());
    map.on("movestart", () => bridge.tilesReady(false));
    map.on("moveend", tileState);
    tiles.addTo(map);
    map.setView([39.98,116.34],13);
    map.on("mousedown", event => {
      if (!drawing || event.originalEvent.button !== 0) return;
      start = event.latlng;
      bridge.boundsChanged("null");
      rectangleFor(L.latLngBounds(start,start));
      L.DomEvent.preventDefault(event.originalEvent);
    });
    map.on("mousemove", event => {
      if (start) rectangle.setBounds(L.latLngBounds(start,event.latlng));
    });
    document.addEventListener("mouseup", complete);
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && drawing) {
        drawMode(false);
        bridge.drawingFinished();
      }
    });
    window.TrafficForgeArea = {
      draw: drawMode,
      clear: () => {
        drawMode(false);
        if(rectangle) { rectangle.remove(); rectangle = null; }
        bridge.boundsChanged("null");
        bridge.drawingFinished();
      },
      setLanguage: value => { language=value; document.documentElement.lang=value; message(); },
      setBounds: value => {
        const b=L.latLngBounds([value.south,value.west],[value.north,value.east]);
        rectangleFor(b); map.fitBounds(b,{padding:[60,60],maxZoom:16}); emitBounds();
      },
      locate: value => { drawMode(false); map.setView([value.latitude,value.longitude],15); },
      retry: () => { bridge.tilesReady(false); tiles.redraw(); },
    };
    message();
    bridge.mapReady();
  });
})();
