export class NexMemError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NexMemError";
  }
}

export class NexMemApiError extends NexMemError {
  readonly status: number;
  readonly response: Response;

  constructor(status: number, message: string, response: Response) {
    super(`NexMem API error ${status}: ${message}`);
    this.name = "NexMemApiError";
    this.status = status;
    this.response = response;
  }
}

export class NexMemAuthError extends NexMemApiError {
  constructor(status: number, message: string, response: Response) {
    super(status, message, response);
    this.name = "NexMemAuthError";
  }
}
