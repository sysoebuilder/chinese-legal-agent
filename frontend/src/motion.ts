// 共用弹簧参数，让布局切换、弹窗和消息使用一致的节奏。
export const spring = {
  type: "spring" as const,
  stiffness: 380,
  damping: 36,
  mass: 0.85,
};

export const reveal = {
  duration: 0.24,
  ease: [0.22, 1, 0.36, 1] as const,
};
