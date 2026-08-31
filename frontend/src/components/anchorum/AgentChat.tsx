import { useState } from 'react';

const API_BASE = window.location.origin;

export default function AgentChat() {
  const [messages, setMessages] = useState<{ role: 'user' | 'assistant'; content: string }[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);

  const sendMessage = async () => {
    if (!input.trim()) return;
    const userMsg = input.trim();
    setMessages((prev) => [...prev, { role: 'user', content: userMsg }]);
    setInput('');
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/anchorum/agent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg }),
      });
      const data = await res.json();
      setMessages((prev) => [...prev, { role: 'assistant', content: data.response || 'No response' }]);
    } catch (e) {
      setMessages((prev) => [...prev, { role: 'assistant', content: 'Error contacting agent.' }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '1rem' }}>
      <div style={{ flex: 1, overflowY: 'auto', marginBottom: '1rem' }}>
        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: '0.5rem', textAlign: m.role === 'user' ? 'right' : 'left' }}>
            <span style={{
              background: m.role === 'user' ? 'var(--accent)' : 'var(--bg-elevated)',
              padding: '0.5rem 1rem', borderRadius: '12px', display: 'inline-block',
              maxWidth: '80%', whiteSpace: 'pre-wrap'
            }}>
              {m.content}
            </span>
          </div>
        ))}
        {loading && <div>Thinking…</div>}
      </div>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && sendMessage()}
          placeholder="Ask or instruct…"
          style={{ flex: 1, padding: '0.75rem', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}
        />
        <button onClick={sendMessage} disabled={loading} style={{ padding: '0.75rem 1.5rem', background: 'var(--accent)', color: 'var(--text-inverse)', border: 'none', borderRadius: '8px', cursor: 'pointer' }}>
          Send
        </button>
      </div>
    </div>
  );
}
