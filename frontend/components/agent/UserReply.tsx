export default function UserReply({ content }: { content: string }) {
  return (
    <div className="flex justify-end animate-fade-in">
      <div className="bg-primary text-primary-foreground rounded-2xl rounded-br-md px-4 py-3 max-w-[80%] text-sm leading-relaxed">
        {content}
      </div>
    </div>
  );
}
