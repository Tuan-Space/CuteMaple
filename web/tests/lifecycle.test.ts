import assert from 'node:assert/strict';
import test from 'node:test';
import { RendererLifecycle } from '../src/lifecycle';

test('a failed/context-lost load cannot announce ready after its async work resolves', () => {
  const lifecycle = new RendererLifecycle();
  const pending = lifecycle.begin();
  assert.equal(lifecycle.fail(), true);
  assert.equal(lifecycle.ready(pending), false);
  assert.equal(lifecycle.fail(), false);
  assert.equal(lifecycle.phase, 'failed');
});
test('explicit replacement invalidates old completion while accepting its own result', () => {
  const lifecycle = new RendererLifecycle();
  const old = lifecycle.begin(), replacement = lifecycle.begin();
  assert.equal(lifecycle.ready(old), false);
  assert.equal(lifecycle.ready(replacement), true);
});
test('stop permanently invalidates pending work in a document', () => {
  const lifecycle = new RendererLifecycle();
  const pending = lifecycle.begin();
  lifecycle.stop();
  assert.equal(lifecycle.ready(pending), false);
  assert.equal(lifecycle.fail(), false);
});
