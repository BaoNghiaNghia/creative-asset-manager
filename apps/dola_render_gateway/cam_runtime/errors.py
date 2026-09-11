class GatewayError(Exception):
 def __init__(self,code,message,status_code=400): self.code,self.message,self.status_code=code,message,status_code
