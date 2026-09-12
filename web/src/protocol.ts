export const REQUIRED_STATES = ['idle', 'walk_right', 'walk_left', 'drag_left', 'drag_right',
  'happy', 'talk', 'petting', 'fall_float', 'land', 'climb_right', 'climb_left',
  'swing_cycle', 'swing_idle', 'sleep_enter', 'sleep_loop', 'sleep_exit',
  ] as const;

export interface PlayCommand {
  type: 'play'; name: string; token: number; playback: 'loop' | 'one_shot' | 'counted_loop';
  cycles?: number; durationMs?: number; fade?: number;
}
export interface ClimbEndpointCommand {
  type: 'climb-endpoint'; name: 'climb_left' | 'climb_right'; token: number; request: number; enabled: boolean;
}
export interface ClimbControlCommand {
  type: 'climb-control'; name: 'climb_left' | 'climb_right'; token: number;
  run: number; resting: boolean;
}
export const EFFECT_NAMES = ['keyboard', 'audio', 'happy', 'leaf', 'note', 'star', 'heart',
  'bubble', 'petal', 'sleep'] as const;
export type EffectName = typeof EFFECT_NAMES[number];
export interface EffectCommand {
  type: 'effect'; name: EffectName; token: number; x?: number; y?: number; intensity?: number;
}
export interface MotionContext {
  type: 'context'; token: number; grounded: boolean; attached: boolean; dragging: boolean;
  falling: boolean; vx: number; vy: number; visibleRect: [number, number, number, number]; effectsEnabled: boolean;
}
export type Command = PlayCommand | ClimbControlCommand
  | EffectCommand | MotionContext | ClimbEndpointCommand
  | { type: 'configure'; modelUrl: string; generation?: number }
  | { type: 'stop'; generation?: number }
  | { type: 'gaze'; x: number; y: number; enabled: boolean }
  | { type: 'pause'; paused: boolean }
  | { type: 'expression'; name: string; active: boolean; intensity?: number; token?: number }
  | { type: 'resize'; width: number; height: number; scale: number };
export type Report = { type: string; [key: string]: unknown };
export type Reporter = (event: Report) => void;
export type Point = [number, number];
export interface Geometry { bounds: [number, number, number, number]; anchors: Record<string, Point>; headBounds?: [number, number, number, number]; transitionProgress?: number; climbPhase?: number }

export function parseCommand(raw: string): Command {
  if (raw.length > 16384) throw new Error('Renderer command is too large.');
  const command = JSON.parse(raw);
  if (!command || typeof command !== 'object' || typeof command.type !== 'string')
    throw new Error('Invalid renderer command.');
  if (command.type === 'play' && (typeof command.name !== 'string' || !Number.isSafeInteger(command.token)
    || !['loop', 'one_shot', 'counted_loop'].includes(command.playback)))
    throw new Error('Invalid motion command.');
  if (command.generation !== undefined && !Number.isSafeInteger(command.generation))
    throw new Error('Invalid renderer generation.');
  if (command.type === 'expression' && command.name?.startsWith('swing_petting_')
    && (!['swing_petting_left', 'swing_petting_right'].includes(command.name)
      || !Number.isSafeInteger(command.token) || typeof command.active !== 'boolean'))
    throw new Error('Invalid swing petting command.');
  if (command.type === 'climb-endpoint' && (!['climb_left', 'climb_right'].includes(command.name)
      || !Number.isSafeInteger(command.token) || !Number.isSafeInteger(command.request) || command.request < 1
      || typeof command.enabled !== 'boolean')) throw new Error('Invalid climb endpoint command.');
  if (command.type === 'climb-control' && (!['climb_left', 'climb_right'].includes(command.name)
      || !Number.isSafeInteger(command.token) || !Number.isSafeInteger(command.run) || command.run < 1
      || typeof command.resting !== 'boolean')) throw new Error('Invalid climb control command.');
  if (command.type === 'effect' && (!EFFECT_NAMES.includes(command.name) || !Number.isSafeInteger(command.token)
    || ['x', 'y', 'intensity'].some(key => command[key] !== undefined && !Number.isFinite(command[key]))
    || ((command.x === undefined) !== (command.y === undefined)))) throw new Error('Invalid effect command.');
  if (command.type === 'context' && (!Number.isSafeInteger(command.token)
    || ['grounded', 'attached', 'dragging', 'falling', 'effectsEnabled'].some(key => typeof command[key] !== 'boolean')
    || !Number.isFinite(command.vx) || !Number.isFinite(command.vy)
    || !Array.isArray(command.visibleRect) || command.visibleRect.length !== 4
    || !command.visibleRect.every(Number.isFinite) || command.visibleRect[2] < 0 || command.visibleRect[3] < 0))
    throw new Error('Invalid motion context.');
  return command as Command;
}

export function localAssetUrl(reference: string, base: string): string {
  const url = new URL(reference, base);
  const origin = new URL(base);
  if (url.protocol !== origin.protocol || url.host !== origin.host || url.username || url.password)
    throw new Error('Model assets must use the local renderer origin.');
  if (!['cutemaple:', 'http:', 'https:'].includes(url.protocol))
    throw new Error('Unsupported model asset URL.');
  return url.href;
}
