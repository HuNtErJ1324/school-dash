import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Conversation, type ConversationStart, type Plan, type TodayPlan, type UploadKind } from "../api";

const POLL_MS = 2_500; // while the agent is working on something you're looking at
const REFRESH_MS = 60_000;

export function useAgent() {
  return useQuery({ queryKey: ["agent"], queryFn: api.agent, refetchInterval: REFRESH_MS });
}

export function useTodayPlan() {
  return useQuery({
    queryKey: ["plan", "today"],
    queryFn: api.todayPlan,
    refetchInterval: (q) => (q.state.data?.pending?.status === "running" ? POLL_MS : REFRESH_MS),
  });
}

export function useMakePlan() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (note: string) => api.makePlan(note),
    onSuccess: (plan) => {
      client.setQueryData<TodayPlan>(["plan", "today"], (old) => ({ plan: old?.plan ?? null, pending: plan }));
      return client.invalidateQueries({ queryKey: ["agent"] });
    },
  });
}

/** Tick a plan task. Shows at once; rolls back if the server refuses. */
export function useTaskDone() {
  const client = useQueryClient();
  const key = ["plan", "today"];
  return useMutation({
    mutationFn: ({ plan, index, done }: { plan: Plan; index: number; done: boolean }) => api.setTaskDone(plan.id, index, done),
    onMutate: async ({ plan, index, done }) => {
      await client.cancelQueries({ queryKey: key });
      const previous = client.getQueryData<TodayPlan>(key);
      client.setQueryData<TodayPlan>(key, (old) =>
        old?.plan?.id === plan.id
          ? { ...old, plan: { ...old.plan, tasks: old.plan.tasks.map((t, i) => (i === index ? { ...t, done } : t)) } }
          : old,
      );
      return { previous };
    },
    onError: (_e, _v, context) => client.setQueryData(key, context?.previous),
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  });
}

/** Ids of the items that have a brief. */
export function useBriefIds() {
  return useQuery({
    queryKey: ["briefs"],
    queryFn: api.briefs,
    refetchInterval: 5 * REFRESH_MS,
    select: (briefs) => new Set(briefs.map((b) => b.item_id)),
  });
}

export function useItemBrief(itemId: number) {
  return useQuery({
    queryKey: ["brief", itemId],
    queryFn: () => api.itemBrief(itemId),
    refetchInterval: (q) => (q.state.data?.pending?.status === "running" ? POLL_MS : false),
  });
}

export function useWriteBrief(itemId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.writeBrief(itemId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["briefs"] });
      return client.invalidateQueries({ queryKey: ["brief", itemId] });
    },
  });
}

export function useConversations() {
  return useQuery({ queryKey: ["conversations"], queryFn: api.conversations, refetchInterval: 5 * REFRESH_MS });
}

const answering = (c: Conversation | undefined) => c?.messages.at(-1)?.status === "running";

export function useConversation(id: number | null) {
  return useQuery({
    queryKey: ["conversation", id],
    queryFn: () => api.conversation(id!),
    enabled: id !== null,
    refetchInterval: (q) => (answering(q.state.data) ? POLL_MS : false),
  });
}

/** Ask in a conversation, or start one (about an item, if given). Resolves to the conversation. */
export function useAsk() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, text, start, photos = [] }: { id: number | null; text: string; start?: ConversationStart; photos?: string[] }) =>
      id === null ? api.startConversation(text, start, photos) : api.sendMessage(id, text, photos),
    onSuccess: (conv) => {
      client.setQueryData(["conversation", conv.id], conv);
      return client.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

export function useDeleteConversation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.deleteConversation(id),
    onSuccess: () => client.invalidateQueries({ queryKey: ["conversations"] }),
  });
}

export function useLibrary() {
  return useQuery({
    queryKey: ["library"],
    queryFn: api.library,
    refetchInterval: (q) => (q.state.data?.running ? POLL_MS * 2 : 5 * REFRESH_MS),
  });
}

export function useLibraryChange() {
  const client = useQueryClient();
  const done = () => client.invalidateQueries({ queryKey: ["library"] });
  return {
    sync: useMutation({ mutationFn: api.syncLibrary, onSuccess: done }),
    upload: useMutation({
      mutationFn: ({ file, ...fields }: { file: File; course_id: string; kind: UploadKind; title: string }) => api.upload(file, fields),
      onSuccess: done,
    }),
    addText: useMutation({ mutationFn: api.addText, onSuccess: done }),
    remove: useMutation({ mutationFn: api.deleteDoc, onSuccess: done }),
  };
}
