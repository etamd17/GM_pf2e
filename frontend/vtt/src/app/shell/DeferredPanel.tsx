import { Component, Suspense, useEffect, useState, type ReactNode } from 'react';

class PanelBoundary extends Component<{children:ReactNode},{failed:boolean}> {
  override state={failed:false};
  static getDerivedStateFromError() {return {failed:true};}
  override render() {
    return this.state.failed ? <div role="alert"><p>This panel could not load. The tabletop may have been updated. Check your connection, then reload this page.</p><button type="button" onClick={() => globalThis.location.reload()}>Reload</button></div> : this.props.children;
  }
}

// Don't fetch a hidden tab's chunk at startup. After first use, leave its
// subtree mounted so drafts, pairing codes and in-flight work survive tabs.
export function DeferredPanel({active,children}:{active:boolean;children:ReactNode}) {
  const [visited,setVisited]=useState(active);
  useEffect(()=>{if(active) setVisited(true);},[active]);
  if(!active && !visited) return null;
  return <PanelBoundary><Suspense fallback={<p role="status">Loading panel...</p>}>{children}</Suspense></PanelBoundary>;
}
