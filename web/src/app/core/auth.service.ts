import { Injectable, inject } from '@angular/core';
import { Observable, from, of, BehaviorSubject } from 'rxjs';
import { map, tap, catchError } from 'rxjs/operators';
import { environment } from '../../environments/environment';
import { SupabaseClient, createClient, Session, AuthChangeEvent, User as SupabaseUser } from '@supabase/supabase-js';

export interface User {
  id: string;
  email: string;
  role?: string;
  [key: string]: unknown;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private supabase: SupabaseClient | null = null;
  private userSubject = new BehaviorSubject<User | null>(null);
  public user$ = this.userSubject.asObservable();
  private initialized = false;

  init(): Promise<void> {
    if (this.initialized) return Promise.resolve();
    
    if (!environment.supabaseUrl || !environment.supabaseAnonKey) {
      console.warn('Supabase not configured, skipping auth init');
      this.initialized = true;
      return Promise.resolve();
    }

    this.supabase = createClient(environment.supabaseUrl, environment.supabaseAnonKey);
    this.initialized = true;

    // Get initial session
    return this.assertSupabase().auth.getSession().then(({ data: { session } }) => {
      if (session?.user) {
        this.setUser(session.user);
      }
    });

    // Listen for auth changes
    this.assertSupabase().auth.onAuthStateChange((_event: AuthChangeEvent, session: Session | null) => {
      if (session?.user) {
        this.setUser(session.user);
      } else {
        this.clearUser();
      }
    });
  }

  private setUser(user: SupabaseUser) {
    const u: User = {
      id: user.id,
      email: user.email ?? '',
      role: user.role || (user.user_metadata as Record<string, unknown>)?.['role'] as string | undefined,
    };
    this.userSubject.next(u);
    localStorage.setItem('supabase_token', user.id);
  }

  private assertSupabase(): SupabaseClient {
    if (!this.supabase) {
      throw new Error('Supabase client not initialized');
    }
    return this.supabase;
  }

  private clearUser() {
    this.userSubject.next(null);
    localStorage.removeItem('supabase_token');
  }

  get currentUser(): User | null {
    return this.userSubject.value;
  }

  get isAuthenticated(): boolean {
    return !!this.userSubject.value;
  }

  signInWithEmail(email: string): Observable<void> {
    if (!this.supabase) {
      return of(void 0);
    }
    return from(this.supabase.auth.signInWithOtp({ email })).pipe(
      map(({ error }) => {
        if (error) throw error;
      })
    );
  }

  signInWithOAuth(provider: 'google' | 'github'): Observable<void> {
    if (!this.supabase) {
      return of(void 0);
    }
    return from(this.supabase.auth.signInWithOAuth({ provider })).pipe(
      map(({ error }) => {
        if (error) throw error;
      })
    );
  }

  signOut(): Observable<void> {
    const client = this.assertSupabase();
    return from(client.auth.signOut()).pipe(
      tap(() => this.clearUser()),
      catchError((err) => {
        this.clearUser();
        throw err;
      }),
      map(() => void 0)
    );
  }

  getSession(): Observable<string | null> {
    if (!this.supabase) {
      return of(null);
    }
    return from(this.supabase.auth.getSession()).pipe(
      map(({ data: { session } }) => session?.access_token ?? null)
    );
  }
}