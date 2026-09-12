/** A failed load stays terminal until its owner explicitly starts a new load. */
export class RendererLifecycle {
  private revision = 0;
  phase: 'new' | 'loading' | 'ready' | 'failed' | 'stopped' = 'new';
  begin(): number { this.phase = 'loading'; return ++this.revision; }
  current(revision: number): boolean {
    return revision === this.revision && (this.phase === 'loading' || this.phase === 'ready');
  }
  ready(revision: number): boolean {
    if (!this.current(revision)) return false;
    this.phase = 'ready'; return true;
  }
  fail(): boolean {
    if (this.phase === 'failed' || this.phase === 'stopped') return false;
    ++this.revision; this.phase = 'failed'; return true;
  }
  stop(): void { ++this.revision; this.phase = 'stopped'; }
}
