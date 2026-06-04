"""
Encryption utilities for securing sensitive payment data
"""
import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from django.conf import settings

from core.secrets import get_secret

# Get encryption key from settings or environment
def get_encryption_key():
    """
    Get or generate the encryption key
    
    The key is derived from the SECRET_KEY using PBKDF2
    """
    # Use Django's SECRET_KEY as the password
    password = settings.SECRET_KEY.encode()
    
    # Use a static salt (store this securely in production)
    salt = get_secret('ENCRYPTION_SALT', 'payment_encryption_salt').encode()
    
    # Generate a key using PBKDF2
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    
    key = base64.urlsafe_b64encode(kdf.derive(password))
    return key

# Create a Fernet cipher with the encryption key
def get_cipher():
    """Get the Fernet cipher for encryption/decryption"""
    key = get_encryption_key()
    return Fernet(key)

def encrypt_data(data):
    """
    Encrypt sensitive data
    
    Args:
        data (str): The data to encrypt
        
    Returns:
        bytes: The encrypted data
    """
    if not data:
        return None
        
    cipher = get_cipher()
    return cipher.encrypt(data.encode())

def decrypt_data(encrypted_data):
    """
    Decrypt encrypted data
    
    Args:
        encrypted_data (bytes): The encrypted data
        
    Returns:
        str: The decrypted data
    """
    if not encrypted_data:
        return None
        
    cipher = get_cipher()
    return cipher.decrypt(encrypted_data).decode()

def mask_card_number(card_number):
    """
    Mask a card number, showing only the last 4 digits
    
    Args:
        card_number (str): The full card number
        
    Returns:
        str: The masked card number (e.g., **** **** **** 1234)
    """
    if not card_number:
        return ""
        
    # Remove spaces
    card_number = card_number.replace(" ", "")
    
    # Get the last 4 digits
    last4 = card_number[-4:]
    
    # Create the mask
    masked_part = "*" * (len(card_number) - 4)
    
    # Format with spaces for readability
    masked = f"{masked_part}{last4}"
    
    # Add spaces for readability (groups of 4)
    if len(masked) == 16:  # Standard credit card
        return f"{masked[:4]} {masked[4:8]} {masked[8:12]} {masked[12:]}"
    else:
        return masked
