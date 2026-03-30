import { useCallback, useEffect, useRef, useState } from "react";
import { io, Socket } from "socket.io-client";

type IceServer = { urls: string | string[] };

const DEFAULT_ICE: IceServer[] = [{ urls: "stun:stun.l.google.com:19302" }];

function getConfig(): { roomId: string } {
  const w = typeof window !== "undefined" ? (window as Window & { __CALL_ROOM__?: { roomId: string; isInitiator: boolean } }) : undefined;
  const c = w?.__CALL_ROOM__;
  return { roomId: c?.roomId || "" };
}

export function CallRoom() {
  const { roomId } = getConfig();
  const [status, setStatus] = useState("Подключение…");
  const [error, setError] = useState<string | null>(null);
  const [remoteOnline, setRemoteOnline] = useState(false);

  const localRef = useRef<HTMLVideoElement>(null);
  const remoteRef = useRef<HTMLVideoElement>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const socketRef = useRef<Socket | null>(null);
  const makingOfferRef = useRef(false);
  const iceQueueRef = useRef<RTCIceCandidateInit[]>([]);

  const hangUp = useCallback(() => {
    try {
      socketRef.current?.disconnect();
    } catch {
      /* ignore */
    }
    socketRef.current = null;
    try {
      pcRef.current?.close();
    } catch {
      /* ignore */
    }
    pcRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (localRef.current) localRef.current.srcObject = null;
    if (remoteRef.current) remoteRef.current.srcObject = null;
    window.location.href = "/chats";
  }, []);

  useEffect(() => {
    if (!roomId) {
      setError("Не указана комната звонка.");
      setStatus("Ошибка");
      return;
    }

    let cancelled = false;
    const socket = io({
      path: "/socket.io/",
      transports: ["websocket", "polling"],
      withCredentials: true,
      reconnection: true,
      reconnectionAttempts: 8,
    });
    socketRef.current = socket;

    const flushIce = async (pc: RTCPeerConnection) => {
      const q = iceQueueRef.current.splice(0, iceQueueRef.current.length);
      for (const c of q) {
        try {
          await pc.addIceCandidate(new RTCIceCandidate(c));
        } catch {
          /* ignore */
        }
      }
    };

    const setupPeer = (iceServers: IceServer[]) => {
      const pc = new RTCPeerConnection({ iceServers: iceServers.length ? iceServers : DEFAULT_ICE });
      pcRef.current = pc;

      pc.onicecandidate = (ev) => {
        if (ev.candidate && socket.connected) {
          socket.emit("ice-candidate", {
            roomId,
            candidate: ev.candidate.toJSON ? ev.candidate.toJSON() : ev.candidate,
          });
        }
      };

      pc.ontrack = (ev) => {
        const [stream] = ev.streams;
        if (remoteRef.current && stream) {
          remoteRef.current.srcObject = stream;
          setRemoteOnline(true);
          setStatus("В сети");
        }
      };

      pc.onconnectionstatechange = () => {
        const st = pc.connectionState;
        if (st === "connected") {
          setStatus("В сети");
          setRemoteOnline(true);
        } else if (st === "disconnected" || st === "failed" || st === "closed") {
          setRemoteOnline(false);
          if (st === "failed") setStatus("Сбой соединения");
        }
      };

      return pc;
    };

    socket.on("joined", async (payload: { peerCount?: number; iceServers?: IceServer[] }) => {
      if (cancelled) return;
      const ice = payload?.iceServers?.length ? payload.iceServers : DEFAULT_ICE;
      const pc = setupPeer(ice);
      const stream = streamRef.current;
      if (!stream) return;
      stream.getTracks().forEach((tr) => pc.addTrack(tr, stream));

      if ((payload?.peerCount ?? 0) < 2) {
        setStatus("Ожидание собеседника…");
      }
    });

    socket.on("peer_ready", async (payload: { isInitiator?: boolean }) => {
      if (cancelled) return;
      const pc = pcRef.current;
      if (!pc) return;
      const iAmCaller = !!payload?.isInitiator;
      if (!iAmCaller) {
        setStatus("Подключение…");
        return;
      }
      try {
        makingOfferRef.current = true;
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        makingOfferRef.current = false;
        socket.emit("call-user", {
          roomId,
          offer: { type: pc.localDescription?.type, sdp: pc.localDescription?.sdp },
        });
        setStatus("Подключение…");
      } catch (err) {
        makingOfferRef.current = false;
        setError(err instanceof Error ? err.message : "Ошибка создания предложения");
      }
    });

    socket.on("call-user", async (data: { roomId?: string; offer?: RTCSessionDescriptionInit }) => {
      if (cancelled || !data?.offer) return;
      const pc = pcRef.current;
      if (!pc) return;
      try {
        if (makingOfferRef.current) return;
        await pc.setRemoteDescription(new RTCSessionDescription(data.offer));
        await flushIce(pc);
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        socket.emit("answer-call", {
          roomId,
          answer: { type: pc.localDescription?.type, sdp: pc.localDescription?.sdp },
        });
        setStatus("Подключение…");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Ошибка ответа");
      }
    });

    socket.on("answer-call", async (data: { answer?: RTCSessionDescriptionInit }) => {
      if (cancelled || !data?.answer) return;
      const pc = pcRef.current;
      if (!pc) return;
      try {
        await pc.setRemoteDescription(new RTCSessionDescription(data.answer));
        await flushIce(pc);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Ошибка установки answer");
      }
    });

    socket.on("ice-candidate", async (data: { candidate?: RTCIceCandidateInit }) => {
      if (cancelled || !data?.candidate) return;
      const pc = pcRef.current;
      if (!pc) return;
      try {
        if (!pc.remoteDescription) {
          iceQueueRef.current.push(data.candidate);
          return;
        }
        await pc.addIceCandidate(new RTCIceCandidate(data.candidate));
      } catch {
        iceQueueRef.current.push(data.candidate);
      }
    });

    socket.on("call_error", (data: { message?: string }) => {
      setError(data?.message || "Ошибка сигналинга");
      setStatus("Ошибка");
    });

    socket.on("disconnect", () => {
      if (!cancelled) setStatus("Отключено");
    });

    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: true,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (localRef.current) {
          localRef.current.srcObject = stream;
          try {
            await localRef.current.play();
          } catch {
            /* autoplay */
          }
        }
      } catch (e) {
        const msg =
          e instanceof DOMException && e.name === "NotAllowedError"
            ? "Доступ к камере/микрофону отклонён. Разрешите в настройках браузера."
            : e instanceof Error
              ? e.message
              : "Не удалось включить камеру или микрофон.";
        setError(msg);
        setStatus("Ошибка");
        return;
      }

      const join = () => {
        if (!cancelled) socket.emit("join_room", { roomId });
      };
      if (socket.connected) join();
      else socket.once("connect", join);
    })();

    return () => {
      cancelled = true;
      try {
        socket.disconnect();
      } catch {
        /* ignore */
      }
      try {
        pcRef.current?.close();
      } catch {
        /* ignore */
      }
      pcRef.current = null;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      socketRef.current = null;
    };
  }, [roomId]);

  return (
    <div className="nf-call-wrap">
      <div className="nf-call-head">
        <span className={`nf-call-status ${remoteOnline ? "ok" : ""}`}>{status}</span>
      </div>
      <div className="nf-call-videos">
        <div className="nf-call-video-box nf-local">
          <video ref={localRef} playsInline muted autoPlay />
          <span className="nf-call-label">Вы</span>
        </div>
        <div className="nf-call-video-box">
          <video ref={remoteRef} playsInline autoPlay />
          <span className="nf-call-label">Собеседник</span>
        </div>
      </div>
      {error ? <div className="nf-call-err">{error}</div> : null}
      <div className="nf-call-actions">
        <button type="button" className="nf-call-end" onClick={hangUp}>
          Завершить звонок
        </button>
      </div>
    </div>
  );
}
