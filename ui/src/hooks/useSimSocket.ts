import { useEffect, useRef, useCallback } from 'react';
import { useSimStore } from '../store/simStore';
import type { SimState, WsOutMessage } from '../types';

const WS_URL = 'ws://localhost:8000/ws';

export function useSimSocket() {
  const setSimState = useSimStore((s) => s.setSimState);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const connect = () => {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onmessage = (e) => {
        const state: SimState = JSON.parse(e.data);
        setSimState(state);
      };

      ws.onclose = () => {
        setTimeout(connect, 2000);
      };

      ws.onerror = () => ws.close();
    };

    connect();
    return () => wsRef.current?.close();
  }, [setSimState]);

  const send = useCallback((msg: WsOutMessage) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  return { send };
}
