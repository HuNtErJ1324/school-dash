// Small stroke icons that inherit the text color. Decorative: buttons carry their own labels.

function Icon({ d, size = 16, weight = 1.6 }: { d: string; size?: number; weight?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={weight}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}

export const CheckIcon = () => <Icon d="M3.5 8.5l3 3 6-7" />;
export const DotsIcon = () => <Icon d="M3.5 8h.01M8 8h.01M12.5 8h.01" weight={2.6} />;
export const PinIcon = () => <Icon d="M6 2.5h4M7 2.5v4L4.5 9.5h7L9 6.5v-4M8 9.5v4" />;
export const NoteIcon = () => <Icon d="M10.5 2.5l3 3L6 13H3v-3l7.5-7.5zM9 4l3 3" />;
export const HideIcon = () => <Icon d="M2 8s2.2-4 6-4 6 4 6 4-2.2 4-6 4-6-4-6-4zM2.5 2.5l11 11" />;
export const GearIcon = () => (
  <Icon d="M8 5.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5zM8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1" />
);
export const CloseIcon = () => <Icon d="M4 4l8 8M12 4l-8 8" />;
export const GripIcon = () => <Icon d="M6 4h.01M10 4h.01M6 8h.01M10 8h.01M6 12h.01M10 12h.01" weight={2.4} />;
export const PlusIcon = () => <Icon d="M8 3v10M3 8h10" />;
export const OpenIcon = () => <Icon d="M9.5 2.5h4v4M13.5 2.5L7 9M11.5 9.5v3a1 1 0 01-1 1h-7a1 1 0 01-1-1v-7a1 1 0 011-1h3" />;
export const SplitIcon = () => <Icon d="M8 2.5v4M8 6.5L4 10v3.5M8 6.5l4 3.5v3.5" />;
export const BookIcon = () => <Icon d="M2.5 3.5c2-.8 3.8-.6 5.5.6 1.7-1.2 3.5-1.4 5.5-.6v9c-2-.8-3.8-.6-5.5.6-1.7-1.2-3.5-1.4-5.5-.6v-9zM8 4.1v9" />;
export const ChatIcon = () => <Icon d="M2.5 4a1.5 1.5 0 011.5-1.5h8A1.5 1.5 0 0113.5 4v5.5A1.5 1.5 0 0112 11H7l-3 2.5V11a1.5 1.5 0 01-1.5-1.5V4z" />;
export const UploadIcon = () => <Icon d="M8 10.5V2.5M5 5.5l3-3 3 3M2.5 10.5v2a1 1 0 001 1h9a1 1 0 001-1v-2" />;
export const TrashIcon = () => <Icon d="M2.5 4.5h11M6 4.5V3h4v1.5M4 4.5l.7 8.5a1 1 0 001 .9h4.6a1 1 0 001-.9l.7-8.5" />;
export const RefreshIcon = () => <Icon d="M13 3.5v3h-3M3 12.5v-3h3M12.4 6.5A4.5 4.5 0 004.2 5M3.6 9.5a4.5 4.5 0 008.2 1.5" />;
export const CameraIcon = () => <Icon d="M2.5 5.5a1 1 0 011-1h2l1-1.5h3l1 1.5h2a1 1 0 011 1v6a1 1 0 01-1 1h-9a1 1 0 01-1-1v-6zM8 10.5a2 2 0 100-4 2 2 0 000 4z" />;
export const CanvasIcon = () => (
  <Icon d="M2.5 3a.5.5 0 01.5-.5h10a.5.5 0 01.5.5v7.5a.5.5 0 01-.5.5H3a.5.5 0 01-.5-.5V3zM5.5 13.5L8 11l2.5 2.5M5 8.5c1-2.5 2-2.5 2.5-1s1.5 1.5 3.5-2" />
);
