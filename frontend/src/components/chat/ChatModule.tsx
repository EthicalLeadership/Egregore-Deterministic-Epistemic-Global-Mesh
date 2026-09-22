/**
 * Chat module — the Egregore Chat (WebSocket) embedded in the FPI console.
 * Speaks the same protocol as static/chat/index.html: messages sent as plain
 * text, responses arrive as JSON envelopes { type, command, ok, summary,
 * detail, suggestion, error }. Auth rides the api_key cookie.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

interface ChatEntry {
  id: string;
  kind: 'user' | 'assistant' | 'system';
  text: string;
  detail?: unknown;
  ok?: boolean;
}

type ConnState = 'connecting' | 'connected' | 'disconnected';

export function ChatModule() {
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [input, setInput] = useState('');
  const [conn, setConn] = useState<ConnState>('disconnected');
  const [busy, setBusy] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const idRef = useRef(0);

  const nextId = () => `c${++idRef.current}`;

  const push = useCallback((entry: Omit<ChatEntry, 'id'>) => {
    setEntries((prev) => [...prev, { ...entry, id: nextId() }]);
  }, []);

  // connect/disconnect lifecycle
  useEffect(() => {
    const sessionId = 'console-' + Math.random().toString(36).slice(2, 10);
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss://' : 'ws://'}${window.location.host}/ws/chat/${sessionId}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    setConn('connecting');

    ws.onopen = () => setConn('connected');
    ws.onclose = () => {
      setConn('disconnected');
      setBusy(false);
    };
    ws.onerror = () => {
      setConn('disconnected');
      push({ kind: 'system', text: 'WebSocket error. Is the Egregore app running?' });
    };
    ws.onmessage = (event) => {
      setBusy(false);
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'error') {
          push({ kind: 'system', text: 'Error: ' + data.error });
        } else if (data.type === 'chat') {
          push({
            kind: 'assistant',
            text: data.summary ?? '',
            detail: data.detail,
            ok: data.ok !== false,
          });
        } else {
          push({ kind: 'assistant', text: event.data });
        }
      } catch {
        push({ kind: 'assistant', text: event.data });
      }
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [push]);

  // auto-scroll to the newest message
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries]);

  const send = useCallback(() => {
    const text = input.trim();
    const ws = wsRef.current;
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
    push({ kind: 'user', text });
    setInput('');
    setBusy(true);
    ws.send(text);
  }, [input, push]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-[#1a2540] px-3 py-1.5">
        <span className="text-[10px] font-medium tracking-[0.18em] text-slate-400 uppercase">
          Chat & Dispatch
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              conn === 'connected'
                ? 'bg-emerald-400'
                : conn === 'connecting'
                  ? 'bg-amber-400'
                  : 'bg-red-500'
            }`}
          />
          <span className="font-mono text-[9px] tracking-[0.14em] text-slate-500 uppercase">
            {conn}
          </span>
        </span>
      </div>

      <div ref={listRef} className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-2">
        {entries.length === 0 && (
          <div className="border border-dashed border-[#1a2540] p-2 text-[10px] leading-relaxed text-slate-500">
            Message the active model, or use commands:
            <span className="block font-mono text-[9px] text-cyan-400">
              /ask · /model · /agents · /agent NAME CMD · /ingest · /compare · /integrity · /hold · /dossier
            </span>
          </div>
        )}
        {entries.map((e) =>
          e.kind === 'user' ? (
            <div key={e.id} className="flex justify-end">
              <div className="max-w-[85%] border border-[#1a2540] bg-[#14203a] px-2.5 py-1.5 text-[11px] text-slate-200">
                {e.text}
              </div>
            </div>
          ) : e.kind === 'system' ? (
            <div key={e.id} className="text-center text-[10px] italic text-slate-600">
              {e.text}
            </div>
          ) : (
            <div key={e.id} className="flex justify-start">
              <div className="max-w-[92%] border border-[#1a2540] bg-[#0d1626] px-2.5 py-1.5 text-[11px] leading-relaxed text-slate-300">
                {e.text && <div className="whitespace-pre-wrap">{e.text}</div>}
                {e.detail !== undefined && e.detail !== null && (
                  <pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-[9.5px] text-slate-500">
                    {JSON.stringify(e.detail, null, 2)}
                  </pre>
                )}
              </div>
            </div>
          )
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1.5 border-t border-[#1a2540] px-2 py-1.5">
        <input
          value={input}
          onChange={(ev) => setInput(ev.target.value)}
          onKeyDown={(ev) => { if (ev.key === 'Enter') send(); }}
          placeholder="Message…"
          disabled={conn !== 'connected'}
          className="min-w-0 flex-1 bg-[#0d1626] px-2 py-1 font-mono text-[10.5px] text-slate-200 placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-cyan-400/60 disabled:opacity-40"
        />
        <button
          onClick={send}
          disabled={conn !== 'connected' || busy || !input.trim()}
          className="border border-[#1a2540] px-2.5 py-1 font-mono text-[10px] text-cyan-300 hover:border-cyan-400/60 disabled:opacity-40"
        >
          {busy ? '…' : 'SEND'}
        </button>
      </div>
    </div>
  );
}
