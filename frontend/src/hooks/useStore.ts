import { useSyncExternalStore } from 'react';
import { store, type DerivedState } from '../lib/store';

export function useStore(): DerivedState {
  return useSyncExternalStore(
    (cb) => store.subscribe(cb),
    () => store.state
  );
}
