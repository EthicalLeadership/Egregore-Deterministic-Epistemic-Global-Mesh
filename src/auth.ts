import { sign, verify } from 'jsonwebtoken';
import dotenv from 'dotenv';

dotenv.config();

const JWT_SECRET = process.env.JWT_SECRET;

if (!JWT_SECRET) {
  throw new Error('JWT_SECRET must be set in environment variables');
}

export function generateToken(payload: object): string {
  return sign(payload, JWT_SECRET, { expiresIn: '1h' });
}

export function verifyToken(token: string): any {
  try {
    return verify(token, JWT_SECRET);
  } catch (err) {
    throw new Error('Invalid token');
  }
}
