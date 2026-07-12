export default {
  async fetch(request: Request, env: ExecutionContext): Promise<Response> {
    return new Response('Not found', { status: 404 });
  },
};
