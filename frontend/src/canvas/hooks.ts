import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type CanvasAskKind, type CanvasSnapshot, type CanvasSummary } from "../api";

export function useCanvases() {
  return useQuery({ queryKey: ["canvases"], queryFn: api.canvases, refetchInterval: 5 * 60_000 });
}

/** One canvas, loaded once: after that the page owns its drawing and saves it as you go. */
export function useCanvas(id: number) {
  return useQuery({ queryKey: ["canvas", id], queryFn: () => api.canvas(id), staleTime: Infinity, gcTime: 0 });
}

export function useCreateCanvas() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.createCanvas,
    onSuccess: () => client.invalidateQueries({ queryKey: ["canvases"] }),
  });
}

export function useRenameCanvas() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: number; title: string }) => api.renameCanvas(id, title),
    onSuccess: (canvas) =>
      client.setQueryData<CanvasSummary[]>(["canvases"], (list) => list?.map((c) => (c.id === canvas.id ? canvas : c))),
  });
}

export function useDeleteCanvas() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.deleteCanvas,
    onSuccess: () => client.invalidateQueries({ queryKey: ["canvases"] }),
  });
}

/** Ask the tutor from a canvas. Resolves to its conversation, which then polls until the answer is in. */
export function useCanvasAsk(canvasId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { kind: CanvasAskKind; text?: string; photos?: string[]; snapshot?: CanvasSnapshot }) =>
      api.askCanvas(canvasId, body),
    onSuccess: (conv) => {
      client.setQueryData(["conversation", conv.id], conv);
      return client.invalidateQueries({ queryKey: ["canvases"] });
    },
  });
}
