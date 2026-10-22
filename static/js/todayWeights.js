// Fork: how the Today insight ranks (src/insight_weights.py): the weights table with
// Reset, and the answer to "why is this first?". Stub from the Phase 3 skeleton.
// Routes: routes/insight_weights_routes.py.

/** Builds the collapsed "How Today ranks" table into `container`. */
export function mountWeights(container) {
  void container;
}

/** Renders a `GET /api/insight/today/why` answer into `container`. */
export function renderWhy(container, data) {
  container.textContent = (data && data.text) || '';
}
