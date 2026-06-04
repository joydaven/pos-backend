#!/usr/bin/env python
"""
Script to switch between development and production environments
for the ShipStation integration.
"""

import os
import shutil
import sys
import argparse

def switch_environment(env_type):
    """
    Switch between development and production environments.
    
    Args:
        env_type (str): Either 'dev' or 'prod'
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    if env_type not in ['dev', 'prod']:
        print("Error: Environment type must be either 'dev' or 'prod'")
        return False
    
    source_file = os.path.join(base_dir, f'.env.{env_type}')
    target_file = os.path.join(base_dir, '.env')
    
    if not os.path.exists(source_file):
        print(f"Error: Source environment file {source_file} does not exist")
        return False
    
    try:
        # Make a backup of the current .env file if it exists
        if os.path.exists(target_file):
            backup_file = os.path.join(base_dir, '.env.backup')
            shutil.copy2(target_file, backup_file)
            print(f"Backed up current .env to {backup_file}")
        
        # Copy the selected environment file to .env
        shutil.copy2(source_file, target_file)
        print(f"Successfully switched to {env_type.upper()} environment")
        return True
    except Exception as e:
        print(f"Error switching environments: {str(e)}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Switch between development and production environments')
    parser.add_argument('env', choices=['dev', 'prod'], help='Environment to switch to (dev or prod)')
    
    args = parser.parse_args()
    success = switch_environment(args.env)
    
    if success:
        print("Environment switched successfully!")
    else:
        print("Failed to switch environment.")
        sys.exit(1)
