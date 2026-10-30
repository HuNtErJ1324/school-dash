import { useEffect, useRef, useState, type ReactNode } from "react";

import { api, photoUrl, type Message } from "../api";
import { chatText } from "../canvas/reply";
import { Link } from "../router";
import { CameraIcon, CloseIcon } from "../ui/icons";
import { unlock, useReadAloud } from "../voice/player";
import { MicButton, ReadAloudToggle, SpeakButton, useReadNewReplies, useVoiceTools } from "../voice/Voice";
import { useAgent, useAsk, useConversation } from "./hooks";
import { Markdown } from "./Markdown";

/**
 * A conversation with the agent: its messages, and a box to ask the next question. With no
 * conversation yet, the first question starts one (about `itemId`, if given). The Tutor page
 * shows the same conversations full size.
 */
export function Chat({ conversationId, onStarted, itemId, placeholder }: {
  conversationId: number | null;
  onStarted: (id: number) => void;
  itemId?: number;
  placeholder: string;
}) {
  const agent = useAgent();
  const conv = useConversation(conversationId);
  const ask = useAsk();
  const end = useRef<HTMLDivElement>(null);
  const count = conv.data?.messages.length ?? 0;
  const seen = useRef(count);

  // Follow new messages as they arrive, but don't jump the page when a conversation first loads.
  useEffect(() => {
    if (count > seen.current) end.current?.scrollIntoView({ block: "nearest" });
    seen.current = count;
  }, [count]);

  const messages = conversationId === null ? [] : (conv.data?.messages ?? []);
  const answering = messages.at(-1)?.status === "running";

  return (
    <div className="chat">
      {messages.length > 0 && <MessageList messages={messages} />}
      <div ref={end} />
      {agent.data && !agent.data.ready ? (
        <p className="quiet">{agent.data.problem ?? "The agent isn't set up."}</p>
      ) : (
        <Composer
          placeholder={placeholder}
          busy={answering || ask.isPending}
          onSend={(text, _photos, done) =>
            ask.mutate(
              { id: conversationId, text, start: { itemId } },
              {
                onSuccess: (c) => {
                  done();
                  if (conversationId === null) onStarted(c.id);
                },
              },
            )
          }
        />
      )}
      {ask.isError && <p className="problem">{ask.error.message}</p>}
      {conversationId !== null && (
        <Link to={`/tutor/${conversationId}`} className="text-button chat-open">
          Continue on the Tutor page
        </Link>
      )}
    </div>
  );
}

export function MessageList({ messages }: { messages: Message[] }) {
  useReadNewReplies(messages);
  return (
    <ol className="chat-messages">
      {messages.map((m) => (
        <li key={m.id} className={`msg msg-${m.role}`}>
          {m.role === "user" ? (
            <>
              {m.photos.length > 0 && (
                <span className="msg-photos">
                  {m.photos.map((id) => (
                    <a key={id} href={photoUrl(id)} target="_blank" rel="noreferrer">
                      <img src={photoUrl(id)} alt="Attached photo" loading="lazy" />
                    </a>
                  ))}
                </span>
              )}
              <p>{m.text}</p>
            </>
          ) : m.status === "running" ? (
            <p className="working">Thinking. When it reads course documents this can take a minute or two</p>
          ) : m.status === "error" ? (
            <p className="problem">{m.error}</p>
          ) : (
            <>
              <SpeakButton message={m} />
              <Markdown text={chatText(m.text)} />
            </>
          )}
        </li>
      ))}
    </ol>
  );
}

const MAX_PHOTOS = 4;

interface Attaching {
  key: string;
  preview: string; // object URL of the picked file
  id?: string; // once uploaded
  error?: string;
}

/**
 * The question box. Enter sends, Shift+Enter starts a new line. `done` clears it once sent.
 * With `photos`, a camera button attaches up to four photos (on a phone it offers the camera);
 * each uploads as soon as it's picked, and a message can be just photos (sent as `photoOnlyText`).
 * `tools` are more buttons, stacked above it. Once the speech container is set up, a mic button beside Send puts
 * what you say in the box (`voiceContext`, what the conversation is about, helps it spell the course's terms), and
 * another reads replies aloud as they arrive.
 */
export function Composer({
  onSend,
  busy,
  placeholder,
  rows = 2,
  label = "Your question",
  autoFocus = false,
  photos = false,
  photoOnlyText = "Here's my work.",
  submitLabel = "Send",
  tools,
  voiceContext,
}: {
  onSend: (text: string, photos: string[], done: () => void) => void;
  busy: boolean;
  placeholder: string;
  rows?: number;
  label?: string;
  autoFocus?: boolean;
  photos?: boolean;
  photoOnlyText?: string;
  submitLabel?: string;
  tools?: ReactNode;
  voiceContext?: string;
}) {
  const [text, setText] = useState("");
  const [voiceProblem, setVoiceProblem] = useState<string | null>(null);
  const voice = useVoiceTools();
  const readAloud = useReadAloud();
  const box = useRef<HTMLTextAreaElement>(null);
  const [attached, setAttached] = useState<Attaching[]>([]);
  const picker = useRef<HTMLInputElement>(null);
  const previews = useRef<string[]>([]);
  useEffect(() => () => previews.current.forEach((url) => URL.revokeObjectURL(url)), []);

  const uploading = attached.some((a) => !a.id && !a.error);
  const ready = attached.flatMap((a) => (a.id ? [a.id] : []));
  const update = (key: string, change: Partial<Attaching>) =>
    setAttached((list) => list.map((a) => (a.key === key ? { ...a, ...change } : a)));

  const pick = (files: FileList | null) => {
    for (const file of [...(files ?? [])].slice(0, MAX_PHOTOS - attached.length)) {
      const key = `${file.name}-${file.size}-${Math.random()}`;
      const preview = URL.createObjectURL(file);
      previews.current.push(preview);
      setAttached((list) => [...list, { key, preview }]);
      api.uploadPhoto(file).then(
        (photo) => update(key, { id: photo.id }),
        (e: Error) => update(key, { error: e.message }),
      );
    }
    if (picker.current) picker.current.value = "";
  };
  const heard = (said: string) => {
    setText((t) => (t.trim() ? `${t.trimEnd()} ${said}` : said));
    if (window.matchMedia("(min-width: 761px)").matches) box.current?.focus(); // not on phones: it would open the keyboard
  };
  const unattach = (key: string) => setAttached((list) => list.filter((a) => a.key !== key));

  const send = () => {
    const question = text.trim() || (ready.length ? photoOnlyText : "");
    if ((!question && !ready.length) || busy || uploading) return;
    if (readAloud) unlock(); // a tap, so the reply can be read when it comes
    setVoiceProblem(null);
    onSend(question, ready, () => {
      setText("");
      setAttached([]);
    });
  };
  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
    >
      {voiceProblem && <p className="problem composer-problem">{voiceProblem}</p>}
      {attached.length > 0 && (
        <ul className="attaching" aria-label="Photos to send">
          {attached.map((a) => (
            <li key={a.key} className={a.error ? "attaching-failed" : a.id ? undefined : "attaching-busy"} title={a.error}>
              <img src={a.preview} alt="" />
              {!a.id && <span className="attaching-state">{a.error ? "Failed" : "Uploading"}</span>}
              <button type="button" className="attaching-remove" aria-label="Remove this photo" onClick={() => unattach(a.key)}>
                <CloseIcon />
              </button>
            </li>
          ))}
        </ul>
      )}
      {(photos || tools) && (
        <div className={tools ? "composer-tools stacked" : "composer-tools"}>
          {tools}
          {photos && (
            <>
              <input ref={picker} type="file" accept="image/*" multiple hidden onChange={(e) => pick(e.target.files)} />
              <button
                type="button"
                className="icon-button composer-photo"
                aria-label="Attach a photo of your work"
                title="Attach a photo of your work"
                disabled={attached.length >= MAX_PHOTOS}
                onClick={() => picker.current?.click()}
              >
                <CameraIcon />
              </button>
            </>
          )}
        </div>
      )}
      <textarea
        ref={box}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
        rows={rows}
        maxLength={8000}
        placeholder={placeholder}
        aria-label={label}
        autoFocus={autoFocus}
      />
      {(voice.mic || voice.readAloud) && (
        <div className={voice.mic && voice.readAloud ? "composer-tools stacked" : "composer-tools"}>
          {voice.mic && <MicButton onText={heard} onProblem={setVoiceProblem} context={voiceContext} />}
          {voice.readAloud && <ReadAloudToggle />}
        </div>
      )}
      <button type="submit" className="pill-button" disabled={(!text.trim() && !ready.length) || busy || uploading}>
        {submitLabel}
      </button>
    </form>
  );
}
