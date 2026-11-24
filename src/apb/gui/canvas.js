// Node canvas: a thin Vue wrapper around Drawflow (spec 06 §3.2).
//
// The canvas is only a view. User actions are reported to Python as events; Python is the
// source of truth and calls the methods below to change the view (or to undo a change it
// rejects). Changes made by Python are "quiet": they do not echo back as events.
import "drawflow";

function portIndex(cls) {
  return parseInt(String(cls).split("_")[1], 10) - 1;
}

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

export default {
  template: `<div class="apb-canvas" tabindex="0"></div>`,
  props: { graph: Object },

  mounted() {
    const editor = new window.Drawflow(this.$el);
    editor.reroute = false;
    editor.curvature = 0.35;
    editor.zoom_min = 0.3;
    editor.zoom_max = 2.0;
    // Wheel: scroll pans, Ctrl + wheel / pinch zooms around the pointer (spec 06 GUI-37).
    // Drawflow's own handler (zoom on Ctrl + wheel only) is switched off before start().
    editor.zoom_enter = () => {};
    editor.start();
    this.$el.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
    this.editor = editor;
    this.byBlock = {}; // block id -> {nid, spec}
    this.byNode = {}; // Drawflow node id (string) -> spec
    this.silent = 0;
    if (this.graph) this.load_graph(this.graph);

    editor.on("nodeMoved", (nid) => {
      const spec = this.byNode[String(nid)];
      if (!spec) return;
      const node = editor.getNodeFromId(nid);
      this.$emit("block_moved", { id: spec.id, x: node.pos_x, y: node.pos_y });
    });
    editor.on("nodeSelected", (nid) => {
      const spec = this.byNode[String(nid)];
      this.$emit("block_selected", { id: spec ? spec.id : null });
    });
    editor.on("nodeUnselected", () => this.$emit("block_selected", { id: null }));
    editor.on("nodeRemoved", (nid) => {
      const spec = this.byNode[String(nid)];
      delete this.byNode[String(nid)];
      if (spec) delete this.byBlock[spec.id];
      if (!this.silent && spec) this.$emit("block_removed", { id: spec.id });
    });
    editor.on("connectionCreated", (c) => {
      if (!this.silent) this.$emit("connection_created", this.describe(c));
    });
    editor.on("connectionRemoved", (c) => {
      if (!this.silent) this.$emit("connection_removed", this.describe(c));
    });
    this.$emit("ready");
  },

  methods: {
    describe(c) {
      const src = this.byNode[String(c.output_id)];
      const dst = this.byNode[String(c.input_id)];
      return {
        source_block: src ? src.id : null,
        source_port: src ? src.outputs[portIndex(c.output_class)].name : null,
        target_block: dst ? dst.id : null,
        target_port: dst ? dst.inputs[portIndex(c.input_class)].name : null,
      };
    },

    nodeHtml(spec) {
      const rows = (ports, cls) =>
        ports.map((p) => `<div class="${cls}" title="${esc(p.type)}">${esc(p.label)}</div>`).join("");
      const sub = `<div class="apb-sub">${esc(spec.type_title)}</div>`;
      return (
        `<div class="apb-head"><div class="apb-label">${esc(spec.label)}</div>${sub}</div>` +
        `<div class="apb-ports"><div class="apb-ins">${rows(spec.inputs, "apb-in")}</div>` +
        `<div class="apb-outs">${rows(spec.outputs, "apb-out")}</div></div>`
      );
    },

    decorate(nid, spec) {
      const el = document.getElementById(`node-${nid}`);
      if (!el) return;
      const mark = (ports, side) =>
        ports.forEach((p, i) => {
          const dot = el.querySelector(`.${side}s .${side}_${i + 1}`);
          if (!dot) return;
          dot.classList.add(`apb-port-${p.type}`);
          const extra = (p.multiple ? ", several connectors" : "") + (p.optional ? ", optional" : "");
          dot.title = `${p.label}: ${p.type}${extra}`;
        });
      mark(spec.inputs, "input");
      mark(spec.outputs, "output");
    },

    quiet(fn) {
      this.silent += 1;
      try {
        fn();
      } finally {
        this.silent -= 1;
      }
    },

    addQuiet(spec) {
      const nid = this.editor.addNode(
        spec.id, spec.inputs.length, spec.outputs.length, spec.x, spec.y,
        `apb-node apb-cat-${spec.category}`, {}, this.nodeHtml(spec),
      );
      this.byBlock[spec.id] = { nid: String(nid), spec };
      this.byNode[String(nid)] = spec;
      this.decorate(nid, spec);
    },

    connectQuiet(c) {
      const src = this.byBlock[c.source_block];
      const dst = this.byBlock[c.target_block];
      if (!src || !dst) return;
      const out = src.spec.outputs.findIndex((p) => p.name === c.source_port);
      const inp = dst.spec.inputs.findIndex((p) => p.name === c.target_port);
      if (out < 0 || inp < 0) return;
      this.editor.addConnection(src.nid, dst.nid, `output_${out + 1}`, `input_${inp + 1}`);
    },

    // ---- methods called from Python ------------------------------------------------------

    load_graph(graph) {
      this.quiet(() => {
        this.editor.clear();
        this.byBlock = {};
        this.byNode = {};
        graph.nodes.forEach((n) => this.addQuiet(n));
        graph.connections.forEach((c) => this.connectQuiet(c));
      });
      // Connection paths are computed from the rendered port positions: redraw them once
      // the browser has laid out the page, then fit the pipeline into the window.
      requestAnimationFrame(() => setTimeout(() => {
        this.refresh_connections();
        this.fit_view();
      }, 60));
    },

    refresh_connections() {
      Object.values(this.byBlock).forEach((e) => this.editor.updateConnectionNodes(`node-${e.nid}`));
    },

    onWheel(e) {
      e.preventDefault();
      const ed = this.editor;
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? this.$el.clientHeight : 1;
      let dx = e.deltaX * unit;
      let dy = e.deltaY * unit;
      if (e.ctrlKey) {
        // Pinch (sent as Ctrl + wheel) or Ctrl + mouse wheel: zoom, keeping the point
        // under the pointer fixed. screen = c + (p - c) * zoom + translate.
        const capped = Math.max(-40, Math.min(40, dy));
        const zoom = Math.max(ed.zoom_min, Math.min(ed.zoom_max, ed.zoom * Math.exp(-capped * 0.005)));
        if (zoom === ed.zoom) return;
        const rect = this.$el.getBoundingClientRect();
        const sx = e.clientX - rect.left - rect.width / 2;
        const sy = e.clientY - rect.top - rect.height / 2;
        ed.canvas_x = sx - (sx - ed.canvas_x) * (zoom / ed.zoom);
        ed.canvas_y = sy - (sy - ed.canvas_y) * (zoom / ed.zoom);
        ed.zoom = zoom;
        ed.zoom_last_value = zoom;
        ed.dispatch("zoom", zoom);
      } else {
        if (e.shiftKey && dx === 0) [dx, dy] = [dy, 0]; // Shift + mouse wheel: sideways
        ed.canvas_x -= dx;
        ed.canvas_y -= dy;
      }
      this.applyTransform();
    },

    applyTransform() {
      const ed = this.editor;
      ed.precanvas.style.transform = `translate(${ed.canvas_x}px, ${ed.canvas_y}px) scale(${ed.zoom})`;
    },

    fit_view() {
      const nodes = Object.values(this.byBlock)
        .map((e) => document.getElementById(`node-${e.nid}`))
        .filter((el) => el);
      const ed = this.editor;
      if (!nodes.length) {
        this.reset_view();
        return;
      }
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      nodes.forEach((el) => {
        x0 = Math.min(x0, el.offsetLeft);
        y0 = Math.min(y0, el.offsetTop);
        x1 = Math.max(x1, el.offsetLeft + el.offsetWidth);
        y1 = Math.max(y1, el.offsetTop + el.offsetHeight);
      });
      // Drawflow scales around the canvas centre c: screen = c + (p - c) * zoom + translate.
      const rect = this.$el.getBoundingClientRect();
      const margin = 40;
      const zoom = Math.max(ed.zoom_min, Math.min(1,
        (rect.width - 2 * margin) / (x1 - x0), (rect.height - 2 * margin) / (y1 - y0)));
      const cx = rect.width / 2, cy = rect.height / 2;
      const left = (rect.width - (x1 - x0) * zoom) / 2; // centred horizontally
      ed.zoom = zoom;
      ed.zoom_last_value = zoom;
      ed.canvas_x = left - cx - (x0 - cx) * zoom;
      ed.canvas_y = margin - cy - (y0 - cy) * zoom;
      this.applyTransform();
    },

    add_node(spec) {
      this.quiet(() => this.addQuiet(spec));
    },

    remove_node(blockId) {
      const entry = this.byBlock[blockId];
      if (entry) this.quiet(() => this.editor.removeNodeId(`node-${entry.nid}`));
    },

    update_node(spec) {
      const entry = this.byBlock[spec.id];
      if (!entry) return;
      entry.spec = spec;
      this.byNode[entry.nid] = spec;
      const content = document.querySelector(`#node-${entry.nid} .drawflow_content_node`);
      if (content) content.innerHTML = this.nodeHtml(spec);
      this.editor.drawflow.drawflow[this.editor.module].data[entry.nid].html = this.nodeHtml(spec);
    },

    replace_node(spec, connections) {
      // Ports changed (e.g. Python block): rebuild the node at its position, reconnect.
      const entry = this.byBlock[spec.id];
      this.quiet(() => {
        if (entry) {
          const node = this.editor.getNodeFromId(entry.nid);
          spec = { ...spec, x: node.pos_x, y: node.pos_y };
          this.editor.removeNodeId(`node-${entry.nid}`);
        }
        this.addQuiet(spec);
        connections.forEach((c) => this.connectQuiet(c));
      });
      requestAnimationFrame(() => this.refresh_connections());
    },

    connect(c) {
      this.quiet(() => this.connectQuiet(c));
    },

    disconnect(c) {
      const src = this.byBlock[c.source_block];
      const dst = this.byBlock[c.target_block];
      if (!src || !dst) return;
      const out = src.spec.outputs.findIndex((p) => p.name === c.source_port);
      const inp = dst.spec.inputs.findIndex((p) => p.name === c.target_port);
      this.quiet(() =>
        this.editor.removeSingleConnection(src.nid, dst.nid, `output_${out + 1}`, `input_${inp + 1}`),
      );
    },

    select_node(blockId) {
      document.querySelectorAll(".apb-canvas .drawflow-node.apb-highlight")
        .forEach((el) => el.classList.remove("apb-highlight"));
      const entry = this.byBlock[blockId];
      if (entry) document.getElementById(`node-${entry.nid}`)?.classList.add("apb-highlight");
    },

    set_states(states) {
      Object.entries(states).forEach(([blockId, state]) => {
        const entry = this.byBlock[blockId];
        const el = entry && document.getElementById(`node-${entry.nid}`);
        if (!el) return;
        [...el.classList].filter((c) => c.startsWith("apb-state-")).forEach((c) => el.classList.remove(c));
        el.classList.add(`apb-state-${state}`);
      });
    },

    set_warnings(blockIds) {
      // A small mark on blocks whose log contains a warning (e.g. an analysis with warnings).
      const warn = new Set(blockIds);
      Object.entries(this.byBlock).forEach(([blockId, entry]) => {
        const el = document.getElementById(`node-${entry.nid}`);
        if (el) el.classList.toggle("apb-warn", warn.has(blockId));
      });
    },

    view_center() {
      // Canvas coordinates of the middle of the visible area (to place new blocks).
      // Inverse of screen = c + (p - c) * zoom + translate, for the screen point s = c.
      const ed = this.editor;
      const rect = this.$el.getBoundingClientRect();
      const cx = rect.width / 2, cy = rect.height / 2;
      return { x: cx - ed.canvas_x / ed.zoom - 105, y: cy - ed.canvas_y / ed.zoom - 40 };
    },

    reset_view() {
      const ed = this.editor;
      ed.canvas_x = 0;
      ed.canvas_y = 0;
      ed.zoom = 1;
      ed.zoom_last_value = 1;
      this.applyTransform();
    },

    zoom(direction) {
      if (direction > 0) this.editor.zoom_in();
      else this.editor.zoom_out();
    },
  },
};
