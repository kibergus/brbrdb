import { describe, it, expect } from 'vitest';
import { formatRedirectPath } from './auth_builder.js';

describe('formatRedirectPath', () => {
    it('strips https://brbrdb.brbrkitten.com prefix from full URLs', () => {
        expect(formatRedirectPath('https://brbrdb.brbrkitten.com/gallery?league=rotax')).toBe('/gallery?league=rotax');
        expect(formatRedirectPath('https://brbrdb.brbrkitten.com/about')).toBe('/about');
        expect(formatRedirectPath('https://brbrdb.brbrkitten.com/')).toBe('/');
        expect(formatRedirectPath('https://brbrdb.brbrkitten.com')).toBe('/');
    });

    it('strips http://brbrdb.brbrkitten.com prefix from full URLs', () => {
        expect(formatRedirectPath('http://brbrdb.brbrkitten.com/gallery')).toBe('/gallery');
        expect(formatRedirectPath('http://brbrdb.brbrkitten.com/')).toBe('/');
        expect(formatRedirectPath('http://brbrdb.brbrkitten.com')).toBe('/');
    });

    it('strips current origin prefix if provided', () => {
        expect(formatRedirectPath('http://localhost:5000/drivers', 'http://localhost:5000')).toBe('/drivers');
        expect(formatRedirectPath('http://localhost:5000/', 'http://localhost:5000')).toBe('/');
        expect(formatRedirectPath('http://localhost:5000', 'http://localhost:5000')).toBe('/');
    });

    it('handles relative paths and plain names', () => {
        expect(formatRedirectPath('/gallery')).toBe('/gallery');
        expect(formatRedirectPath('gallery')).toBe('/gallery');
        expect(formatRedirectPath('/league?league=piston_cup')).toBe('/league?league=piston_cup');
    });

    it('handles empty or whitespace inputs', () => {
        expect(formatRedirectPath('')).toBe('/');
        expect(formatRedirectPath('   ')).toBe('/');
        expect(formatRedirectPath(null)).toBe('/');
        expect(formatRedirectPath(undefined)).toBe('/');
    });

    it('preserves external absolute URLs', () => {
        expect(formatRedirectPath('https://example.com/external')).toBe('https://example.com/external');
    });
});
